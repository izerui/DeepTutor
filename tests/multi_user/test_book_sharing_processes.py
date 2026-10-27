"""Independent interpreters, real SQLite locks/journals, bounded crash recovery."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest

from deeptutor.book import registry as books
from deeptutor.knowledge import registry as kbs


WORKER = r"""
import json
import os
from pathlib import Path
import sys
import time
from deeptutor.book import registry as books
from deeptutor.book import sharing
from deeptutor.knowledge import registry as kbs
from deeptutor.multi_user.context import set_current_user
from deeptutor.multi_user.models import CurrentUser, UserScope

root, mode, book_id = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
books.DB_PATH_OVERRIDE = root / "books.sqlite3"
kbs.DB_PATH_OVERRIDE = root / "kbs.sqlite3"
set_current_user(CurrentUser(
    id="root", username="root", role="admin",
    scope=UserScope(kind="admin", user_id="root", root=root),
))
(root / (mode + ".started")).write_text(str(os.getpid()))
original = sharing.sync_protection

def barrier(conn, value, **kwargs):
    original(conn, value, **kwargs)
    (root / (mode + ".ready")).write_text("both registries changed, not committed")
    deadline = time.monotonic() + 15
    while not (root / "release").exists():
        if time.monotonic() >= deadline:
            raise RuntimeError("bounded barrier timed out")
        time.sleep(.01)
    if mode.startswith("crash"):
        os._exit(23)
    if mode.startswith("fail"):
        raise RuntimeError("injected cleanup error after both writes")

try:
    if mode.endswith("cleanup"):
        sharing.sync_protection = barrier
        books.remove_book_references("template", "one")
    elif mode == "crash_publish":
        sharing.sync_protection = barrier
        private = next(iter(books.book_kb_links(book_id).values()))
        books.set_book_visibility(book_id, "public", kb_choices={private: "make_public"})
    elif mode == "prepare_exit":
        books.share_book_kbs(book_id)
        (root / (mode + ".ready")).write_text("standalone preparation returned")
        os._exit(24)
    elif mode == "fail_prepare":
        good = books.register_book(
            base_dir=root / "books", dir_name="book_anew", title="New", created_by="owner",
        )
        bad = books.register_book(
            base_dir=root / "books", dir_name="book_zblocked", title="Blocked", created_by="owner",
        )
        connector = kbs.register_kb(
            base_dir=root / "sources", name="vault", kind="obsidian", created_by="owner",
        )
        books.set_book_kb_links(good.book_id, books.book_kb_links(book_id))
        books.set_book_kb_links(bad.book_id, {"vault": connector.kb_id})
        books.replace_book_references(
            "template", "one", [good.book_id, bad.book_id], created_by="owner",
        )
    elif mode == "fail_commit":
        with books._write() as conn:
            conn.execute("CREATE TABLE commit_parent(id TEXT PRIMARY KEY)")
            conn.execute(
                "CREATE TABLE commit_child("
                "id TEXT REFERENCES commit_parent(id) DEFERRABLE INITIALLY DEFERRED)"
            )
            conn.execute(
                "CREATE TRIGGER fail_commit AFTER UPDATE OF visibility ON book_registry "
                "BEGIN INSERT INTO commit_child VALUES ('missing'); END"
            )
        private = next(iter(books.book_kb_links(book_id).values()))
        books.set_book_visibility(book_id, "public", kb_choices={private: "make_public"})
    elif mode == "publish":
        books.set_book_visibility(book_id, "public")
    elif mode == "reference":
        books.replace_book_references("template", "two", [book_id], created_by="reader")
    elif mode == "path_reference":
        books.replace_book_references("mastery_template", "two", [book_id], created_by="reader")
    elif mode == "withdraw":
        books.set_book_visibility(book_id, "private")
    else:
        raise AssertionError(mode)
    result = {"status": "ok"}
except Exception as exc:
    result = {"status": "error", "error": type(exc).__name__, "message": str(exc)}
(root / (mode + ".result.json")).write_text(json.dumps(result))
print(json.dumps(result), flush=True)
"""


@pytest.fixture
def process_library(tmp_path, monkeypatch, mu_isolated_root):
    monkeypatch.setattr(books, "DB_PATH_OVERRIDE", tmp_path / "books.sqlite3")
    monkeypatch.setattr(kbs, "DB_PATH_OVERRIDE", tmp_path / "kbs.sqlite3")
    kb = kbs.register_kb(
        base_dir=tmp_path / "sources", name="answers", kind="uploaded", created_by="owner",
    )
    book = books.register_book(
        base_dir=tmp_path / "books", dir_name="book_waves", title="Waves", created_by="owner",
    )
    books.set_book_kb_links(book.ref, {"answers": kb.kb_id})
    return tmp_path, book, kb


def start(root, mode, book_id):
    return subprocess.Popen(
        [sys.executable, "-B", "-c", WORKER, str(root), mode, book_id],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def wait_file(path: Path, process):
    deadline = time.monotonic() + 12
    while not path.exists():
        assert process.poll() is None, process.communicate(timeout=2)
        assert time.monotonic() < deadline, f"Timed out waiting for {path}"
        time.sleep(.01)


def finish(process, root, mode, *, code=0):
    out, err = process.communicate(timeout=20)
    (root / f"{mode}.stdout.log").write_text(out)
    (root / f"{mode}.stderr.log").write_text(err)
    assert process.returncode == code, (out, err)
    assert not err, err
    return json.loads(out) if out else None


def stop_all(processes):
    for process in processes:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)


@pytest.mark.parametrize("mode", ["pause_cleanup", "fail_cleanup", "crash_cleanup"])
def test_competing_publication_cannot_lose_kb_protection(process_library, mode):
    root, book, kb = process_library
    books.replace_book_references("template", "one", [book.ref], created_by="owner")
    first = start(root, mode, book.book_id)
    processes = [first]
    try:
        wait_file(root / f"{mode}.ready", first)
        # Verify both *real* databases reject another process-independent connection.
        for path in (books._db_path(), kbs._db_path()):
            conn = sqlite3.connect(path, timeout=0, isolation_level=None)
            try:
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    conn.execute("BEGIN IMMEDIATE")
            finally:
                conn.close()
        second = start(root, "publish", book.book_id)
        processes.append(second)
        wait_file(root / "publish.started", second)
        # The second interpreter actually reached the mutation while the first
        # held both writers; no stale check/cleanup can overtake it.
        time.sleep(.15)
        assert second.poll() is None
        assert not (root / "publish.result.json").exists()
        (root / "release").write_text("continue")
        result = finish(first, root, mode, code=23 if mode == "crash_cleanup" else 0)
        if mode == "fail_cleanup":
            assert result["status"] == "error" and result["error"] == "RuntimeError"
        assert finish(second, root, "publish") == {"status": "ok"}
        assert books.get_book(book.ref).visibility == "public"
        assert [r.kb_id for r in kbs.list_referenced("book", book.book_id)] == [kb.kb_id]
        assert bool(books.list_book_references(book.ref)) == (mode != "pause_cleanup")
        # A later cleanup must derive PUBLIC from the new committed state.
        books.remove_book_references("template", "one")
        assert kbs.list_referenced("book", book.book_id)
    finally:
        stop_all(processes)


def test_crash_publication_recovers_both_databases_and_old_refs(process_library):
    root, book, kb = process_library
    books.replace_book_references("template", "one", [book.ref], created_by="owner")
    before = books.list_book_references(book.ref)
    process = start(root, "crash_publish", book.book_id)
    try:
        wait_file(root / "crash_publish.ready", process)
        (root / "release").write_text("crash with both databases dirty")
        finish(process, root, "crash_publish", code=23)
        # Independent successor opens both files, causing hot-journal recovery.
        successor = start(root, "reference", book.book_id)
        try:
            assert finish(successor, root, "reference") == {"status": "ok"}
        finally:
            stop_all([successor])
        assert books.get_book(book.ref).visibility == "private"
        assert kbs.get_kb(kb.kb_id).visibility == "private"
        assert books.list_book_references(book.ref)[0] == before[0]
        assert len(books.list_book_references(book.ref)) == 2
        assert [r.kb_id for r in kbs.list_referenced("book", book.book_id)] == [kb.kb_id]
    finally:
        stop_all([process])


def test_standalone_preparation_process_death_leaves_no_refs(process_library):
    root, book, kb = process_library
    process = start(root, "prepare_exit", book.book_id)
    try:
        finish(process, root, "prepare_exit", code=24)
        assert (root / "prepare_exit.ready").is_file()
        assert books.get_book(book.ref).visibility == "private"
        assert kbs.get_kb(kb.kb_id).visibility == "private"
        assert not kbs.list_referenced("book", book.book_id)
        successor = start(root, "publish", book.book_id)
        try:
            assert finish(successor, root, "publish") == {"status": "ok"}
        finally:
            stop_all([successor])
        assert kbs.list_referenced("book", book.book_id)
    finally:
        stop_all([process])


def test_withdrawal_checks_reference_committed_by_other_worker(process_library):
    root, book, _ = process_library
    books.set_book_visibility(book.ref, "public")
    process = start(root, "path_reference", book.book_id)
    try:
        assert finish(process, root, "path_reference") == {"status": "ok"}
    finally:
        stop_all([process])
    process = start(root, "withdraw", book.book_id)
    try:
        result = finish(process, root, "withdraw")
        # Rule R1: a teacher-path reference committed by another worker is seen
        # by the withdrawal, which then asks for confirmation and changes nothing.
        assert result["status"] == "error"
        assert result["error"] == "BookWithdrawalNeedsConfirmation"
        assert books.get_book(book.ref).visibility == "public"
        assert books.list_book_references(book.ref)
        assert kbs.list_referenced("book", book.book_id)
    finally:
        stop_all([process])


@pytest.mark.parametrize("mode", ["fail_prepare", "fail_commit"])
def test_process_preparation_and_commit_errors_preserve_old_refs(process_library, mode):
    root, book, kb = process_library
    books.replace_book_references("template", "one", [book.ref], created_by="owner")
    before = books.list_book_references(book.ref)
    process = start(root, mode, book.book_id)
    try:
        result = finish(process, root, mode)
        assert result["status"] == "error"
        assert result["error"] == ("IntegrityError" if mode == "fail_commit" else "BookReferenceError")
        assert books.get_book(book.ref).visibility == "private"
        assert kbs.get_kb(kb.kb_id).visibility == "private"
        assert books.list_book_references(book.ref) == before
        assert [r.kb_id for r in kbs.list_referenced("book", book.book_id)] == [kb.kb_id]
        if mode == "fail_prepare":
            assert not kbs.list_referenced("book", "anew")
            assert not books.list_book_references("anew")
            assert not books.list_book_references("zblocked")
        if mode == "fail_commit":
            with books._write() as conn:
                conn.execute("DROP TRIGGER fail_commit")
        successor = start(root, "publish", book.book_id)
        try:
            assert finish(successor, root, "publish") == {"status": "ok"}
        finally:
            stop_all([successor])
        assert books.get_book(book.ref).visibility == "public"
        assert kbs.list_referenced("book", book.book_id)
    finally:
        stop_all([process])
