"""The "mastery_template" readers reach plans B and D through their real loaders.

The autouse fixture preregisters them for convenience; these tests reset the
reader maps first, so they prove the loaders themselves (cached imports,
per-thread staging, no lock across an import, custom readers kept).
"""

from __future__ import annotations

import json
import subprocess
import sys

from deeptutor.book import registry as books
from deeptutor.knowledge import registry as kbs
from deeptutor.teaching import access


def _private_kb(tmp_path, owner: str):
    return kbs.register_kb(
        base_dir=tmp_path / "kbs", name="answers", kind="uploaded", created_by=owner
    )


def _private_book(tmp_path, owner: str):
    return books.register_book(
        base_dir=tmp_path / "books", dir_name="book_bk_one", title="One", created_by=owner
    )


def test_cached_loaders_restore_both_readers_without_preregistration(
    tmp_path, monkeypatch, make_user
):
    owner = make_user("t1", role="teacher")
    student = make_user("s1", role="student")
    kb = _private_kb(tmp_path, owner.id)
    book = _private_book(tmp_path, owner.id)
    # The module is cached; reset both maps as a fresh test copy would be.
    monkeypatch.setattr(kbs, "_readers", {"partner": kbs._partner_reader})
    monkeypatch.setattr(kbs, "_builtin_readers_loaded", False)
    monkeypatch.setattr(books, "_readers", {})

    assert not kbs.can_read(kb, student)
    assert not books.can_read_book(book, student)

    assert kbs._readers[access.REFERRER_KIND] is access.template_reader
    assert books._readers[access.REFERRER_KIND] is access.template_reader
    assert kbs._readers["book"] is books.book_kb_reader


def test_loaders_and_import_registration_never_replace_a_custom_reader(
    tmp_path, monkeypatch, make_user
):
    owner = make_user("t1", role="teacher")
    student = make_user("s1", role="student")
    kb = _private_kb(tmp_path, owner.id)
    book = _private_book(tmp_path, owner.id)

    def custom(_referrer, _user):
        return False

    monkeypatch.setattr(kbs, "_readers", {})
    monkeypatch.setattr(kbs, "_builtin_readers_loaded", False)
    monkeypatch.setattr(books, "_readers", {})
    kbs.register_reference_reader(access.REFERRER_KIND, custom)
    books.register_book_reference_reader(access.REFERRER_KIND, custom)

    kbs.can_read(kb, student)
    books.can_read_book(book, student)
    books.can_read_book(book, student)  # D's loader runs on every check
    access._register_defaults()

    assert kbs._readers[access.REFERRER_KIND] is custom
    assert books._readers[access.REFERRER_KIND] is custom


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
assert "deeptutor.teaching.access" not in sys.modules
assert "deeptutor.book.registry" not in sys.modules
kbs.DB_PATH_OVERRIDE = root / "kb.sqlite3"
kb = kbs.register_kb(base_dir=root / "kbs", name="answers", kind="uploaded", created_by="owner")

from deeptutor.book import registry as books

books.DB_PATH_OVERRIDE = root / "book.sqlite3"
book = books.register_book(
    base_dir=root / "books", dir_name="book_bk_one", title="One", created_by="owner"
)


def custom(_referrer, _user):
    return False


# An explicit custom book reader registered before anything loads.
books.register_book_reference_reader("mastery_template", custom)

reader = CurrentUser(
    id="reader", username="reader", role="user",
    scope=UserScope(kind="user", user_id="reader", root=root / "reader"),
)
at_registrar = threading.Event()
kb_at_import = threading.Event()
book_at_import = threading.Event()
errors = []
results = []
original_import = importlib.import_module


def traced_import(name, package=None):
    if name == "deeptutor.teaching.access":
        if threading.current_thread().name == "kb-reader":
            kb_at_import.set()
        elif threading.current_thread().name == "book-reader":
            book_at_import.set()
    return original_import(name, package)


importlib.import_module = traced_import


def trace(frame, event, arg):
    if (
        event == "call"
        and frame.f_code.co_name == "_register_defaults"
        and frame.f_globals.get("__name__") == "deeptutor.teaching.access"
    ):
        at_registrar.set()
        assert kb_at_import.wait(5), "kb can_read never reached the cold import"
        assert book_at_import.wait(5), "book can_read never reached the cold import"
    return trace


def direct_import():
    sys.settrace(trace)
    try:
        __import__("deeptutor.teaching.access")
        results.append("direct-import")
    except BaseException as exc:
        errors.append(repr(exc))
    finally:
        sys.settrace(None)


def check_kb():
    try:
        assert not kbs.can_read(kb, reader)
        results.append("kb-read")
    except BaseException as exc:
        errors.append(repr(exc))


def check_book():
    try:
        assert not books.can_read_book(book, reader)
        results.append("book-read")
    except BaseException as exc:
        errors.append(repr(exc))


direct = threading.Thread(target=direct_import, name="direct-import", daemon=True)
kb_thread = threading.Thread(target=check_kb, name="kb-reader", daemon=True)
book_thread = threading.Thread(target=check_book, name="book-reader", daemon=True)
direct.start()
assert at_registrar.wait(5), "the direct cold import never reached its registrar"
kb_thread.start()
book_thread.start()
for thread in (direct, kb_thread, book_thread):
    thread.join(5)
alive = [thread.name for thread in (direct, kb_thread, book_thread) if thread.is_alive()]
if alive:
    faulthandler.dump_traceback()
assert not alive, ("cold import deadlocked", alive)
assert not errors, errors
access = sys.modules["deeptutor.teaching.access"]
assert kbs._readers["mastery_template"] is access.template_reader
assert books._readers["mastery_template"] is custom
assert kbs._builtin_readers_loaded
print(json.dumps({"threads": sorted(results), "custom_kept": True}))
"""


def test_cold_teaching_import_races_both_loaders_without_deadlock(tmp_path):
    """Fresh interpreter, bounded: a direct import, a KB check and a book check race."""
    result = subprocess.run(
        [sys.executable, "-B", "-c", COLD_IMPORT, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    (tmp_path / "cold-import.stdout.log").write_text(result.stdout)
    (tmp_path / "cold-import.stderr.log").write_text(result.stderr)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "threads": ["book-read", "direct-import", "kb-read"],
        "custom_kept": True,
    }
