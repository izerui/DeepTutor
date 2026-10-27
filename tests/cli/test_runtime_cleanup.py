"""CLI completion releases the runtime before closing its event loop."""

import asyncio

import pytest

from deeptutor.app import container as containers
from deeptutor_cli.common import maybe_run


@pytest.mark.parametrize("outcome", ["success", "error", "cancel"])
def test_cli_releases_runtime_on_every_exit(outcome):
    container = containers.get_application_container()

    async def command():
        await container.start()
        if outcome == "error":
            raise ValueError("command failed")
        if outcome == "cancel":
            raise asyncio.CancelledError
        return "done"

    if outcome == "success":
        assert maybe_run(command()) == "done"
    else:
        error = ValueError if outcome == "error" else asyncio.CancelledError
        with pytest.raises(error):
            maybe_run(command())
    assert not container._started
    assert containers._default_container is None


def test_cleanup_does_not_construct_an_unused_container(monkeypatch):
    monkeypatch.setattr(
        containers.ApplicationContainer,
        "build",
        lambda: pytest.fail("Cleanup constructed a runtime"),
    )

    async def command():
        return 42

    assert maybe_run(command()) == 42
