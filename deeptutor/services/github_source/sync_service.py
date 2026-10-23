"""Background service that periodically syncs GitHub sources into KBs."""

from __future__ import annotations

import logging
from pathlib import Path

from deeptutor.multi_user.context import reset_current_user, set_current_user
from deeptutor.multi_user.paths import local_admin_user
from deeptutor.services.base_sync import BaseSourceSyncService, default_base_dir, is_stale
from deeptutor.services.github_source.sync import SYNC_INTERVAL_HOURS, redact_sync_error

logger = logging.getLogger(__name__)


class GitHubSourceSyncService(BaseSourceSyncService):
    """Periodically syncs all GitHub sources."""

    def __init__(self, *, base_dir=None, client=None, check_interval_s=3600, owner=None):
        if owner is None and base_dir is not None:
            if Path(base_dir).resolve() != Path(default_base_dir()).resolve():
                raise ValueError("A custom GitHub sync base_dir requires an explicit owner.")
        super().__init__(base_dir=base_dir, check_interval_s=check_interval_s)
        self._client = client
        self._owner = owner or local_admin_user()

    @property
    def task_name(self) -> str:
        return "github-source-sync"

    async def _sync_one_cycle(self) -> None:
        if self._base_dir is None:
            from deeptutor.knowledge.registry_scope import indexed_records, owner_user

            for record in indexed_records():
                try:
                    await self._sync_root(record.base_dir, owner_user(record), record=record)
                except Exception:
                    logger.exception("GitHub sync failed for KB %s", record.kb_id)
            return
        await self._sync_root(self.effective_base_dir, self._owner)

    async def _sync_root(self, base_dir, owner, *, record=None) -> None:
        from deeptutor.knowledge.manager import KnowledgeBaseManager
        from deeptutor.services.github_source.sync import sync_source

        token = set_current_user(owner)
        try:
            manager = KnowledgeBaseManager(base_dir=base_dir)

            all_sources = (
                [(record.dir_name, source) for source in manager.get_github_sources(record.dir_name)]
                if record is not None else manager.get_all_github_sources()
            )
            for kb_name, source in all_sources:
                if not source.get("enabled", True):
                    continue
                if not is_stale(source, stale_hours=SYNC_INTERVAL_HOURS):
                    continue
                sid = source.get("id", "?")
                if record is not None:
                    from deeptutor.knowledge import registry
                    from deeptutor.knowledge.registry_scope import is_indexed_source

                    if registry.get_kb(record.kb_id) != record or not is_indexed_source(record):
                        return
                try:
                    await sync_source(
                        kb_name=kb_name,
                        source=source,
                        base_dir=base_dir,
                        client=self._client,
                    )
                except Exception as exc:
                    manager.update_github_source_state(
                        kb_name=kb_name,
                        source_id=sid,
                        last_sync_status="error",
                        last_sync_error=redact_sync_error(exc),
                    )
        finally:
            reset_current_user(token)


_sync_service = None


def get_sync_service():
    global _sync_service
    if _sync_service is None:
        _sync_service = GitHubSourceSyncService()
    return _sync_service
