"""What a teacher's draft may share with students, and how it is snapshotted."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from types import SimpleNamespace

import pytest

from deeptutor.learning.models import (
    KnowledgePoint,
    KnowledgeType,
    LearningModule,
    TopicSource,
    TopicSourceKind,
)
from deeptutor.teaching import kb as kb_access
from deeptutor.teaching import snapshot
from deeptutor.teaching.models import TemplateDocRef, TemplateKbRef, TemplateSources
from deeptutor.teaching.snapshot import (
    SnapshotError,
    outline_issues,
    plan_sources,
    remap_modules,
)

KB = TopicSourceKind.KNOWLEDGE_BASE
FILE = TopicSourceKind.FILE


def _source(index: int, kind: TopicSourceKind, source_id: str = "", **metadata) -> TopicSource:
    return TopicSource(
        id=f"s{index}",
        kind=kind,
        source_id=source_id,
        label=f"Source {index}",
        position=index,
        metadata=metadata,
    )


@pytest.fixture
def teacher(make_user):
    return make_user("u_tina", role="teacher", username="tina")


def test_public_kb_passes_and_is_rewritten_to_its_stable_ref(fake_kb_registry, teacher):
    fake_kb_registry.add("kb1", visibility="public", legacy_refs=("admin:kb:calculus",))

    plan = plan_sources([_source(0, KB, "admin:kb:calculus")], teacher)

    assert plan.issues == []
    assert plan.private_kbs == []
    assert plan.sources[0].source_id == "kb:kb1"
    assert plan.kb_refs == {"kb1": None}
    assert plan.to_sources({}, {}).kb_refs[0].access == "public"


def test_own_private_kb_asks_for_a_choice_defaulting_to_public(fake_kb_registry, teacher):
    fake_kb_registry.add(
        "kb2", visibility="private", created_by="u_tina", legacy_refs=("user:kb:mine",)
    )

    plan = plan_sources([_source(0, KB, "user:kb:mine")], teacher)

    assert plan.issues == []
    assert [(c.key, c.default) for c in plan.private_kbs] == [("kb2", "make_public")]
    assert plan.to_sources({}, {}).kb_refs[0].access == "public"
    assert plan.to_sources({"kb2": "reference_only"}, {}).kb_refs[0].access == "reference_only"


def test_a_republish_defaults_to_the_previous_choice(fake_kb_registry, teacher):
    fake_kb_registry.add(
        "kb2", visibility="private", created_by="u_tina", legacy_refs=("user:kb:mine",)
    )
    previous = TemplateSources(kb_refs=[TemplateKbRef(kb_id="kb2", access="reference_only")])

    plan = plan_sources([_source(0, KB, "user:kb:mine")], teacher, previous=previous)

    assert plan.private_kbs[0].default == "reference_only"
    assert plan.to_sources({}, {}).kb_refs[0].access == "reference_only"


def test_someone_elses_private_kb_blocks_publishing(fake_kb_registry, teacher):
    fake_kb_registry.add("kb3", visibility="private", created_by="u_tom", legacy_refs=("x",))

    plan = plan_sources([_source(0, KB, "x")], teacher)

    assert [issue.code for issue in plan.issues] == ["not_shareable"]


def test_connector_blocks_publishing(fake_kb_registry, teacher):
    fake_kb_registry.add(
        "vault", visibility="private", created_by="u_tina", kind="obsidian", legacy_refs=("v",)
    )

    plan = plan_sources([_source(0, KB, "v")], teacher)

    assert [issue.code for issue in plan.issues] == ["connector"]


def test_missing_and_unindexed_kbs_block_publishing(fake_kb_registry, teacher):
    fake_kb_registry.add("kb4", visibility="public", ready=False, legacy_refs=("slow",))

    plan = plan_sources([_source(0, KB, "gone"), _source(1, KB, "slow")], teacher)

    assert [issue.code for issue in plan.issues] == ["missing_kb", "not_indexed"]


@pytest.mark.parametrize(
    "kind",
    [
        TopicSourceKind.NOTEBOOK,
        TopicSourceKind.CHAT,
        TopicSourceKind.QUESTION_BANK,
        TopicSourceKind.COWRITER,
        TopicSourceKind.PARTNER_GROUP,
    ],
)
def test_personal_sources_block_publishing(fake_kb_registry, teacher, kind):
    plan = plan_sources([_source(0, kind, "personal-1")], teacher)

    assert [issue.code for issue in plan.issues] == ["personal_source"]
    assert plan.sources == []


def test_goal_source_is_kept_as_is(fake_kb_registry, teacher):
    goal = _source(0, TopicSourceKind.GOAL)

    plan = plan_sources([goal], teacher)

    assert plan.issues == []
    assert plan.sources == [goal]


def test_files_are_published_with_fixed_document_ids_and_versions(fake_kb_registry, teacher):
    fake_kb_registry.add("kb5", visibility="public", legacy_refs=("library",))
    fake_kb_registry.write_file("kb5", "notes/a.md")
    fake_kb_registry.write_file("kb5", "b.md")

    plan = plan_sources(
        [
            _source(0, FILE, "notes/a.md", kb_name="library"),
            _source(1, FILE, "", kb_name="library", path="b.md"),
        ],
        teacher,
    )

    # Read-only until publishing: nothing is registered yet.
    assert plan.issues == []
    assert fake_kb_registry.documents == {}
    with pytest.raises(SnapshotError):
        plan.to_sources({}, {})

    plan.register_documents()
    sources = plan.to_sources({}, {})

    (ref,) = sources.kb_refs
    assert ref.kb_id == "kb5"
    assert ref.doc_ids == ["doc_1", "doc_2"]
    assert [(d.doc_id, d.rel_path, d.version) for d in ref.documents] == [
        ("doc_1", "notes/a.md", 1),
        ("doc_2", "b.md", 1),
    ]
    assert sources.sources[0].metadata == {
        "kb_name": "kb:kb5",
        "path": "notes/a.md",
        "doc_id": "doc_1",
        "doc_version": 1,
    }


def test_already_registered_documents_keep_their_ids(fake_kb_registry, teacher):
    fake_kb_registry.add("kb5", visibility="public", legacy_refs=("library",))
    path = fake_kb_registry.write_file("kb5", "a.md")
    existing = kb_access.record_document("kb5", "a.md", path)

    plan = plan_sources([_source(0, FILE, "a.md", kb_name="library")], teacher)
    plan.register_documents()

    assert plan.to_sources({}, {}).kb_refs[0].doc_ids == [existing.doc_id]
    assert len(fake_kb_registry.documents) == 1


def test_a_missing_document_blocks_publishing(fake_kb_registry, teacher):
    fake_kb_registry.add("kb5", visibility="public", legacy_refs=("library",))

    plan = plan_sources([_source(0, FILE, "gone.md", kb_name="library")], teacher)

    assert [issue.code for issue in plan.issues] == ["missing_document"]


def test_a_whole_kb_wins_over_its_documents(fake_kb_registry, teacher):
    fake_kb_registry.add("kb6", visibility="public", legacy_refs=("library",))
    fake_kb_registry.write_file("kb6", "doc-1.md")

    plan = plan_sources(
        [_source(0, FILE, "doc-1.md", kb_name="library"), _source(1, KB, "library")], teacher
    )
    plan.register_documents()

    assert plan.kb_refs == {"kb6": None}
    assert plan.to_sources({}, {}).kb_refs[0].doc_ids is None


def test_a_source_from_another_workspace_is_resolved_there(
    fake_kb_registry, teacher, monkeypatch
):
    fake_kb_registry.add("kb7", visibility="public", legacy_refs=("library",))
    inside: ContextVar[str] = ContextVar("inside", default="")
    seen: list[str] = []

    @contextmanager
    def fake_scope(source):
        token = inside.set(str(source.metadata.get("content_workspace_id") or ""))
        try:
            yield
        finally:
            inside.reset(token)

    real_resolve = kb_access.resolve

    def recording_resolve(ref):
        seen.append(inside.get())
        return real_resolve(ref)

    monkeypatch.setattr(snapshot, "source_scope", fake_scope)
    monkeypatch.setattr(kb_access, "resolve", recording_resolve)

    plan = plan_sources([_source(0, KB, "library", content_workspace_id="ws1")], teacher)

    assert seen == ["ws1"]
    assert "content_workspace_id" not in plan.sources[0].metadata


def test_kb_ready_accepts_old_kbs_without_a_status_when_files_exist(tmp_path):
    record = SimpleNamespace(base_dir=str(tmp_path), dir_name="old")
    (tmp_path / "old" / "raw").mkdir(parents=True)
    assert kb_access.kb_ready(record) is False
    (tmp_path / "old" / "raw" / "a.md").write_text("x", encoding="utf-8")
    assert kb_access.kb_ready(record) is True


def _module(*points: KnowledgePoint) -> LearningModule:
    return LearningModule(id="m0", name="M", order=0, knowledge_points=list(points))


def _kp(kp_id: str, *prerequisites: str) -> KnowledgePoint:
    return KnowledgePoint(
        id=kp_id,
        name=kp_id.title(),
        type=KnowledgeType.CONCEPT,
        module_id="m0",
        prerequisite_ids=list(prerequisites),
    )


def test_remap_modules_applies_aliases_to_ids_and_prerequisites():
    mapped = remap_modules([_module(_kp("draft_a"), _kp("b", "draft_a"))], {"draft_a": "stable_a"})

    assert [kp.id for kp in mapped[0].knowledge_points] == ["stable_a", "b"]
    assert mapped[0].knowledge_points[1].prerequisite_ids == ["stable_a"]


def test_remap_modules_rejects_two_points_on_one_stable_id():
    with pytest.raises(SnapshotError):
        remap_modules([_module(_kp("draft_a"), _kp("stable_a"))], {"draft_a": "stable_a"})


def test_outline_issues_report_empty_outlines_modules_and_cycles():
    assert outline_issues([], []) == ["At least one learning module is required"]
    empty = LearningModule(id="m1", name="Empty", order=0)
    assert outline_issues([empty], []) == ["Module 'm1' must contain at least one knowledge point"]
    cycle = _module(_kp("a", "b"), _kp("b", "a"))
    assert any("cycle" in issue.lower() for issue in outline_issues([cycle], []))


# ── stable document ids and explicit intent, against the real plan B registry ──


def _intent(kb_id: str) -> str | None:
    from deeptutor.core.visibility_intent import explicit_choice
    from deeptutor.knowledge import registry as kb_registry

    with kb_registry._read() as conn:
        if conn is None or not explicit_choice(conn, kb_id, schema="main"):
            return None
        return conn.execute(
            "SELECT value FROM registry_meta WHERE key = ?", ("visibility_intent_v1:" + kb_id,)
        ).fetchone()[0]


@pytest.fixture
def library(tmp_path):
    """A real registered public KB whose ``a.md`` moved after it got its id.

    An unrelated file now occupies the old path, with an identity of its own.
    """
    from deeptutor.knowledge import registry as kb_registry

    base = tmp_path / "real_kbs"
    record = kb_registry.register_kb(
        base_dir=base, name="library", kind="uploaded", created_by="u_tina", visibility="public"
    )
    raw = base / "library" / "raw"
    raw.mkdir(parents=True)
    (raw / "a.md").write_text("original", encoding="utf-8")
    original = kb_access.record_document(record.kb_id, "a.md", raw / "a.md")
    (raw / "moved").mkdir()
    (raw / "a.md").rename(raw / "moved" / "a.md")
    kb_registry.move_document_paths(record.kb_id, "a.md", "moved/a.md")
    (raw / "a.md").write_text("unrelated", encoding="utf-8")
    unrelated = kb_access.record_document(record.kb_id, "a.md", raw / "a.md")
    return SimpleNamespace(record=record, raw=raw, original=original, unrelated=unrelated)


def test_a_document_id_resolves_its_live_path_after_a_move(library):
    assert library.unrelated.doc_id != library.original.doc_id
    assert kb_access.live_document(library.record.kb_id, library.original.doc_id).rel_path == (
        "moved/a.md"
    )
    assert kb_access.document_file(library.record, library.original.doc_id) == (
        library.raw / "moved" / "a.md"
    ).resolve()
    captured = TemplateKbRef(
        kb_id=library.record.kb_id,
        doc_ids=[library.original.doc_id],
        documents=[TemplateDocRef(doc_id=library.original.doc_id, rel_path="a.md", version=1)],
    )
    assert kb_access.missing_documents(captured) == []

    (library.raw / "moved" / "a.md").unlink()

    # The unrelated file at the captured path never stands in for the document.
    assert kb_access.document_file(library.record, library.original.doc_id) is None
    assert [doc.doc_id for doc in kb_access.missing_documents(captured)] == [
        library.original.doc_id
    ]
    # Provenance stays as captured.
    assert captured.documents[0].rel_path == "a.md"


def test_a_missing_kb_makes_every_bound_document_missing():
    captured = TemplateKbRef(
        kb_id="nope",
        doc_ids=["d1"],
        documents=[TemplateDocRef(doc_id="d1", rel_path="a.md", version=1)],
    )
    assert [doc.doc_id for doc in kb_access.missing_documents(captured)] == ["d1"]


def test_a_file_source_with_a_document_id_follows_it_not_the_old_path(library, teacher):
    source = _source(
        0, FILE, "a.md", kb_name=library.record.ref, path="a.md", doc_id=library.original.doc_id
    )

    plan = plan_sources([source], teacher)
    plan.register_documents()
    sources = plan.to_sources({}, {})

    assert plan.issues == []
    assert sources.sources[0].metadata["path"] == "moved/a.md"
    assert sources.sources[0].metadata["doc_id"] == library.original.doc_id
    assert [(d.doc_id, d.rel_path) for d in sources.kb_refs[0].documents] == [
        (library.original.doc_id, "moved/a.md")
    ]
    # The draft itself is untouched.
    assert source.metadata["path"] == "a.md"


def test_a_file_source_whose_document_id_is_gone_blocks(library, teacher):
    (library.raw / "moved" / "a.md").unlink()
    from deeptutor.knowledge import registry as kb_registry

    kb_registry.mark_document_deleted(library.record.kb_id, "moved/a.md")
    source = _source(
        0, FILE, "a.md", kb_name=library.record.ref, path="a.md", doc_id=library.original.doc_id
    )

    plan = plan_sources([source], teacher)

    assert [issue.code for issue in plan.issues] == ["missing_document"]


def test_template_references_merge_revisions_of_one_kb(fake_kb_registry):
    fake_kb_registry.add("kb8")
    kb_access.replace_template_references(
        "tpl",
        [
            TemplateKbRef(kb_id="kb8", doc_ids=["d1"]),
            TemplateKbRef(kb_id="kb8", doc_ids=["d2", "d1"]),
        ],
        created_by="u_tina",
    )
    assert fake_kb_registry.references[("mastery_template", "tpl")] == [("kb8", ["d1", "d2"])]

    kb_access.replace_template_references(
        "tpl",
        [TemplateKbRef(kb_id="kb8", doc_ids=["d1"]), TemplateKbRef(kb_id="kb8", doc_ids=None)],
        created_by="u_tina",
    )
    assert fake_kb_registry.references[("mastery_template", "tpl")] == [("kb8", None)]


def test_explicit_reference_only_records_private_intent_with_the_reference(tmp_path, as_user):
    from deeptutor.knowledge import registry as kb_registry

    base = tmp_path / "intent_kbs"
    mine = kb_registry.register_kb(base_dir=base, name="mine", kind="uploaded", created_by="u_tina")
    other = kb_registry.register_kb(base_dir=base, name="other", kind="uploaded", created_by="u_tina")
    public = kb_registry.register_kb(
        base_dir=base, name="pub", kind="uploaded", created_by="u_tina", visibility="public"
    )
    foreign = kb_registry.register_kb(base_dir=base, name="tom", kind="uploaded", created_by="u_tom")
    refs = [
        TemplateKbRef(kb_id=record.kb_id, access="reference_only")
        for record in (mine, other, foreign)
    ] + [TemplateKbRef(kb_id=public.kb_id)]

    with as_user("u_tina", role="teacher"):
        # Defaults alone are not consent.
        kb_access.replace_template_references("tpl", refs, created_by="u_tina")
        assert {_intent(r.kb_id) for r in (mine, other, public, foreign)} == {None}

        kb_access.replace_template_references(
            "tpl",
            refs,
            created_by="u_tina",
            explicit_reference_only=[mine.kb_id, public.kb_id, foreign.kb_id, "unrelated"],
        )

    assert _intent(mine.kb_id) == "private"
    assert _intent(other.kb_id) is None
    # Never withdraws an already public KB, never speaks for another owner.
    assert _intent(public.kb_id) is None
    assert kb_registry.get_kb(public.kb_id).visibility == "public"
    assert _intent(foreign.kb_id) is None
    assert {ref.kb_id for ref in kb_registry.list_references(mine.kb_id)} == {mine.kb_id}
