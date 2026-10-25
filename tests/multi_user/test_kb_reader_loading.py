"""Cold imports and concurrent registrations must not hold reader-map locks."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from deeptutor.knowledge import registry as kbs


COLD_IMPORT = r"""
import faulthandler
import importlib
import json
from pathlib import Path
import sys
import threading
from deeptutor.knowledge import registry as kbs
from deeptutor.multi_user.models import CurrentUser, UserScope

root = Path(sys.argv[1])
assert "deeptutor.book.registry" not in sys.modules
kbs.DB_PATH_OVERRIDE = root / "kb.sqlite3"
kb = kbs.register_kb(
    base_dir=root / "sources", name="private", kind="uploaded", created_by="owner",
)
reader = CurrentUser(
    id="reader", username="reader", role="user",
    scope=UserScope(kind="user", user_id="reader", root=root / "reader"),
)
direct_at_registrar = threading.Event()
reader_at_import = threading.Event()
errors = []
results = []
original_import = importlib.import_module

def traced_import(name, package=None):
    if name == "deeptutor.book.registry" and threading.current_thread().name == "kb-reader":
        reader_at_import.set()
    return original_import(name, package)

importlib.import_module = traced_import

def trace(frame, event, arg):
    if (
        event == "call"
        and frame.f_code.co_name == "register_kb_reader"
        and frame.f_globals.get("__name__") == "deeptutor.book.registry"
    ):
        direct_at_registrar.set()
        assert reader_at_import.wait(5), "can_read never reached the real builtin import"
    return trace

def direct_import():
    sys.settrace(trace)
    try:
        __import__("deeptutor.book.registry")
        results.append("direct-import-finished")
    except BaseException as exc:
        errors.append(repr(exc))
    finally:
        sys.settrace(None)

def check_read():
    try:
        assert not kbs.can_read(kb, reader)
        results.append("can-read-finished")
    except BaseException as exc:
        errors.append(repr(exc))

direct = threading.Thread(target=direct_import, name="direct-import", daemon=True)
checking = threading.Thread(target=check_read, name="kb-reader", daemon=True)
direct.start()
assert direct_at_registrar.wait(5), "direct cold import never reached the eager registrar"
checking.start()
direct.join(3)
checking.join(3)
alive = [thread.name for thread in (direct, checking) if thread.is_alive()]
if alive:
    faulthandler.dump_traceback()
assert not alive, ("cold import deadlocked", alive)
assert not errors, errors
book = sys.modules["deeptutor.book.registry"]
assert kbs._readers["book"] is book.book_kb_reader
assert kbs._builtin_readers_loaded
assert sorted(results) == ["can-read-finished", "direct-import-finished"]
print(json.dumps({"threads": sorted(results), "reader_registered": True}))
"""


def test_direct_cold_book_import_and_real_can_read_finish(tmp_path):
    result = subprocess.run(
        [sys.executable, "-B", "-c", COLD_IMPORT, str(tmp_path)],
        capture_output=True, text=True, timeout=20,
    )
    (tmp_path / "cold-import.stdout.log").write_text(result.stdout)
    (tmp_path / "cold-import.stderr.log").write_text(result.stderr)
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    assert json.loads(result.stdout) == {
        "threads": ["can-read-finished", "direct-import-finished"],
        "reader_registered": True,
    }


@pytest.mark.parametrize("fails", [False, True])
def test_concurrent_custom_registration_survives_builtin_result(monkeypatch, fails):
    original = lambda *_: False
    candidate = lambda *_: True
    custom = lambda *_: False
    monkeypatch.setattr(kbs, "_readers", {"existing": original})
    monkeypatch.setattr(kbs, "_builtin_readers_loaded", False)
    monkeypatch.setattr(kbs, "BUILTIN_READER_MODULES", ("synthetic.reader",))
    hook_ready = threading.Event()
    release_hook = threading.Event()
    custom_done = threading.Event()
    errors = []

    def hook():
        kbs.register_reference_reader("existing", candidate)
        kbs.register_reference_reader("builtin_only", candidate)
        hook_ready.set()
        assert release_hook.wait(5)
        if fails:
            raise RuntimeError("synthetic hook failure after custom registration")

    monkeypatch.setattr(kbs, "importlib", SimpleNamespace(
        import_module=lambda _: SimpleNamespace(register_kb_reader=hook),
    ))

    def load():
        try:
            kbs._load_builtin_readers()
        except BaseException as exc:
            errors.append(exc)

    def register_custom():
        try:
            kbs.register_reference_reader("existing", custom)
            kbs.register_reference_reader("new_custom", custom)
        except BaseException as exc:
            errors.append(exc)
        finally:
            custom_done.set()

    loading = threading.Thread(target=load, daemon=True)
    registering = threading.Thread(target=register_custom, daemon=True)
    loading.start()
    try:
        assert hook_ready.wait(3)
        registering.start()
        assert custom_done.wait(3), "custom registration waited on an external hook"
        assert kbs._readers["existing"] is custom
        assert kbs._readers["new_custom"] is custom
        # A partially executed hook must not grant access before its success.
        assert "builtin_only" not in kbs._readers
    finally:
        release_hook.set()
        loading.join(3)
        if registering.ident is not None:
            registering.join(3)
    assert not loading.is_alive() and not registering.is_alive()
    assert not errors
    assert kbs._readers["existing"] is custom
    assert kbs._readers["new_custom"] is custom
    assert ("builtin_only" in kbs._readers) is not fails
    assert kbs._builtin_readers_loaded is not fails
    if fails:
        monkeypatch.setattr(kbs, "importlib", SimpleNamespace(
            import_module=lambda _: SimpleNamespace(
                register_kb_reader=lambda: kbs.register_reference_reader("builtin_only", candidate),
            ),
        ))
        kbs._load_builtin_readers()
        assert kbs._builtin_readers_loaded
        assert kbs._readers["builtin_only"] is candidate
        assert kbs._readers["existing"] is custom and kbs._readers["new_custom"] is custom
