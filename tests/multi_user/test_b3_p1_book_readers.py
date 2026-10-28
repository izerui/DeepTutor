"""B3-4: builtin book reference readers load once, like the KB loader contract."""

from __future__ import annotations

import sys
import types

from deeptutor.book import registry


def _private_book(tmp_path, owner="t1"):
    return registry.register_book(
        base_dir=tmp_path / "books", dir_name="book_bk_one", title="One", created_by=owner,
    )


def test_builtin_readers_are_imported_once_across_reads(tmp_path, monkeypatch, make_user):
    book = _private_book(tmp_path)
    student = make_user("s1", role="student")
    imports: list[str] = []
    real_import = registry.importlib.import_module

    def counting_import(name, *args, **kwargs):
        imports.append(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(registry.importlib, "import_module", counting_import)
    for _ in range(3):
        assert not registry.can_read_book(book, student)
    assert imports == list(registry.BUILTIN_BOOK_READER_MODULES)


def test_failed_hook_is_retried_until_it_succeeds_then_loaded_once(
    tmp_path, monkeypatch, make_user,
):
    book = _private_book(tmp_path)
    student = make_user("s1", role="student")
    registry.replace_book_references("course", "c1", [book.ref], created_by="t1")
    module = types.ModuleType("synthetic_b3_p1_book_readers")
    calls: list[bool] = []
    fail = True

    def register_book_readers():
        calls.append(fail)
        registry.register_book_reference_reader("course", lambda _r, _u: True)
        if fail:
            raise RuntimeError("synthetic incomplete hook")

    module.register_book_readers = register_book_readers
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(registry, "BUILTIN_BOOK_READER_MODULES", (module.__name__,))
    monkeypatch.setattr(registry, "_readers", {})
    assert not registry.can_read_book(book, student)
    assert not registry.can_read_book(book, student)
    assert registry._readers == {}
    fail = False
    assert registry.can_read_book(book, student)
    assert registry.can_read_book(book, student)
    assert calls == [True, True, False]


def test_explicit_registration_keeps_precedence_over_a_later_hook(
    tmp_path, monkeypatch, make_user,
):
    book = _private_book(tmp_path)
    student = make_user("s1", role="student")
    registry.replace_book_references("course", "c1", [book.ref], created_by="t1")
    module = types.ModuleType("synthetic_b3_p1_precedence")

    def register_book_readers():
        registry.register_book_reference_reader("course", lambda _r, _u: True)

    module.register_book_readers = register_book_readers
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(registry, "BUILTIN_BOOK_READER_MODULES", (module.__name__,))

    def explicit(_referrer, _user):
        return False

    registry.register_book_reference_reader("course", explicit)
    assert not registry.can_read_book(book, student)
    assert registry._readers["course"] is explicit
