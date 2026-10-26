"""A path whose outline belongs to a teacher says so, and says it cheaply."""

from __future__ import annotations

from deeptutor.learning.models import LearningProgress, PathOrigin, TemplateUpdateNotice
from deeptutor.learning.service import LearningService
from deeptutor.learning.storage import LearningStore


def _stamp(store: LearningStore, path_id: str, origin: PathOrigin) -> None:
    def apply(tx):
        tx.progress.origin = origin
        tx.touch()

    store.mutate(path_id, apply, create=True)


def test_origin_round_trips_through_the_store(tmp_path):
    store = LearningStore(tmp_path)
    origin = PathOrigin(
        template_id="tpl_1",
        revision=2,
        update_notice=TemplateUpdateNotice(from_revision=1, to_revision=2, added=1),
    )
    _stamp(store, "p1", origin)

    loaded = store.load("p1")
    assert loaded.origin == origin
    assert store.load_origin("p1") == origin


def test_paths_without_origin_report_none(tmp_path):
    store = LearningStore(tmp_path)
    store.mutate("p2", lambda tx: tx.touch(), create=True)

    assert store.load_origin("p2") is None
    assert store.load_origin("missing") is None


def test_state_written_before_the_field_existed_still_loads():
    assert LearningProgress.model_validate({"book_id": "legacy"}).origin is None


def test_reset_keeps_the_origin(tmp_path):
    store = LearningStore(tmp_path)
    _stamp(store, "p3", PathOrigin(template_id="tpl_3", revision=1))

    LearningService(store).reset_path("p3")

    assert store.load_origin("p3").template_id == "tpl_3"


def test_topic_payload_exposes_the_origin(tmp_path):
    from deeptutor.api.routers.mastery_path import _topic_payload

    store = LearningStore(tmp_path)
    _stamp(store, "p4", PathOrigin(template_id="tpl_4", revision=3))

    payload = _topic_payload(store, "p4")

    assert payload["origin"]["template_id"] == "tpl_4"
    assert payload["origin"]["revision"] == 3
    assert payload["origin"]["update_notice"] is None
