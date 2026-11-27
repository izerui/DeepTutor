"""An offline workspace is retryable, not an empty or damaged data store."""

from pathlib import Path
import logging

import pytest

from deeptutor.book import registry as books
from deeptutor.book import registry_backfill as book_backfill
from deeptutor.knowledge import registry_backfill as kb_backfill
from deeptutor.multi_user.paths import local_admin_user, user_context
from deeptutor.services.workspace import ContentWorkspaceService


@pytest.mark.parametrize("kind", ["knowledge", "books"])
def test_offline_workspace_keeps_registration_and_retries_when_restored(
    mu_isolated_root, seed_user, monkeypatch, caplog, kind
):
    seed_user("boss", role="admin")
    with user_context(local_admin_user()):
        service = ContentWorkspaceService()
        row = service.create_workspace("Temporarily offline")
        root = Path(row["path"])
        (root / "keep.txt").write_text("Existing learner data")
        stored = service._catalog()
        offline = root.with_name(root.name + "-offline")
        root.rename(offline)
        visited = []
        if kind == "knowledge":
            def scan(path, *, created_by):
                visited.append(path)
                return 1
            monkeypatch.setattr(kb_backfill, "_sync_root", scan)
            run = lambda: kb_backfill._sync_workspaces(created_by="local-admin")
            expected_missing, expected_ready = 0, 1
            logger_name = kb_backfill.__name__
        else:
            def scan(created_by):
                visited.append(created_by)
                return 1, True
            monkeypatch.setattr(book_backfill, "_sync_scope", scan)
            run = lambda: book_backfill._sync_workspaces("local-admin")
            expected_missing, expected_ready = (0, False), (1, True)
            logger_name = book_backfill.__name__

        with caplog.at_level(logging.WARNING, logger=logger_name):
            assert run() == expected_missing
        assert visited == []
        assert not root.exists()
        assert service._catalog() == stored
        records = [record for record in caplog.records if record.name == logger_name]
        assert len(records) == 1
        assert records[0].levelno == logging.WARNING
        assert records[0].exc_info is None
        assert row["workspace_id"] in records[0].getMessage()
        assert str(root) in records[0].getMessage()

        offline.rename(root)
        assert run() == expected_ready
        assert len(visited) == 1
        assert (root / "keep.txt").read_text() == "Existing learner data"


@pytest.mark.parametrize("kind", ["knowledge", "books"])
def test_unexpected_workspace_failures_keep_error_tracebacks(
    mu_isolated_root, seed_user, monkeypatch, caplog, kind
):
    seed_user("boss", role="admin")
    with user_context(local_admin_user()):
        ContentWorkspaceService().create_workspace("Readable")
        def fail(*args, **kwargs):
            raise RuntimeError("Synthetic damaged data")
        if kind == "knowledge":
            monkeypatch.setattr(kb_backfill, "_sync_root", fail)
            assert kb_backfill._sync_workspaces(created_by="local-admin") == 0
            logger_name = kb_backfill.__name__
        else:
            monkeypatch.setattr(book_backfill, "_sync_scope", fail)
            assert book_backfill._sync_workspaces("local-admin") == (0, False)
            logger_name = book_backfill.__name__
        records = [record for record in caplog.records if record.name == logger_name]
        assert any(record.levelno == logging.ERROR and record.exc_info for record in records)


def test_missing_workspace_does_not_mark_book_migration_complete(
    mu_isolated_root, seed_user
):
    seed_user("boss", role="admin")
    with user_context(local_admin_user()):
        row = ContentWorkspaceService().create_workspace("Offline shelf")
        root = Path(row["path"])
        offline = root.with_name(root.name + "-offline")
        root.rename(offline)
        book_backfill.backfill_book_registry()
        assert books.get_meta(book_backfill.SHELF_MIGRATED_KEY) is None
        offline.rename(root)
        book_backfill.backfill_book_registry()
        assert books.get_meta(book_backfill.SHELF_MIGRATED_KEY) is not None
