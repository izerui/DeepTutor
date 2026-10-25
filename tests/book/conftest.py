"""Book agent tests use shipped defaults in a synthetic runtime home."""

import pytest
import yaml


@pytest.fixture(autouse=True)
def book_agent_settings(tmp_path, monkeypatch):
    from deeptutor.services.config import loader
    from deeptutor.services.setup.init import DEFAULT_AGENTS_SETTINGS

    root = tmp_path / "book-agent-settings"
    settings = root / "data" / "user" / "settings"
    settings.mkdir(parents=True)
    (settings / "agents.yaml").write_text(
        yaml.safe_dump(DEFAULT_AGENTS_SETTINGS), encoding="utf-8",
    )
    monkeypatch.setattr(loader, "PROJECT_ROOT", root)
