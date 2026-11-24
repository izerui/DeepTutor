"""API surface tests for ``/api/creations`` (GET draft, POST approve).

Covers: create / 409 conflict / same-session update / already_created
idempotence / owner isolation / ``always: true`` never reaching the saved
SKILL.md / reference files landing in the skill dir. Filesystem access is
isolated the same way ``test_partners_router.py`` isolates
``PartnerDraftStore`` — plus a direct monkeypatch of
``get_skill_service``/``get_persona_service`` (mirroring
``test_capability_routing.py``) so Approve's real publish path never touches
the real workspace machinery.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover
    FastAPI = None
    TestClient = None

pytestmark = pytest.mark.skipif(
    FastAPI is None or TestClient is None, reason="fastapi not installed"
)

from deeptutor.services.creations import drafts as drafts_module
from deeptutor.services.persona.service import PersonaService
from deeptutor.services.skill.service import SkillService


@pytest.fixture
def services(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    user_root = tmp_path / "user-root"
    monkeypatch.setattr(
        drafts_module,
        "get_current_path_service",
        lambda: SimpleNamespace(get_user_root=lambda: user_root),
    )

    skills = SkillService(root=tmp_path / "skills", builtin_root=None)
    personas = PersonaService(root=tmp_path / "personas")
    admin_personas_root = tmp_path / "admin-personas" / "personas"
    monkeypatch.setattr("deeptutor.services.skill.get_skill_service", lambda: skills)
    monkeypatch.setattr("deeptutor.services.persona.get_persona_service", lambda: personas)
    monkeypatch.setattr(
        "deeptutor.multi_user.paths.get_admin_path_service",
        lambda: SimpleNamespace(get_workspace_dir=lambda: admin_personas_root.parent),
    )
    return SimpleNamespace(skills=skills, personas=personas)


@pytest.fixture
def client(services, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from deeptutor.api.routers import auth as auth_module
    import deeptutor.api.routers.creations as creations_router_mod
    from deeptutor.multi_user.context import reset_current_user, set_current_user
    from deeptutor.multi_user.models import CurrentUser, UserScope

    monkeypatch.setattr(auth_module, "AUTH_ENABLED", False)
    token = set_current_user(
        CurrentUser(
            id="learner-1",
            username="learner-1",
            role="user",
            scope=UserScope(kind="user", user_id="learner-1", root=Path("/tmp/learner-1")),
        )
    )
    creations_router_mod._approve_locks.clear()

    app = FastAPI()
    app.include_router(creations_router_mod.router, prefix="/api/creations")
    try:
        yield TestClient(app)
    finally:
        reset_current_user(token)


def _make_draft(*, kind: str = "skill", session_id: str = "session-a", **overrides):
    from deeptutor.services.creations.drafts import CreationDraftStore

    fields = {
        "kind": kind,
        "session_id": session_id,
        "name": "my-skill" if kind == "skill" else "my-persona",
        "description": "A test artifact.",
        "content": "# Body\nStep one.",
        "tags": ["tool"] if kind == "skill" else [],
        "references": [],
        **overrides,
    }
    return CreationDraftStore().create(**fields)


class TestGetDraft:
    def test_not_found(self, client: TestClient) -> None:
        assert client.get("/api/creations/drafts/" + "0" * 32).status_code == 404

    def test_create_state_for_a_free_name(self, client: TestClient) -> None:
        draft = _make_draft()
        response = client.get(f"/api/creations/drafts/{draft.draft_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["approval"] == "create"
        assert body["draft"]["name"] == "my-skill"

    def test_conflict_state_when_name_taken_by_another_session(
        self, client: TestClient, services
    ) -> None:
        services.skills.create("my-skill", "existing", "existing body")
        draft = _make_draft()
        response = client.get(f"/api/creations/drafts/{draft.draft_id}")
        assert response.json()["approval"] == "conflict"

    def test_update_state_for_a_same_session_revision(self, client: TestClient, services) -> None:
        services.skills.create("my-skill", "existing", "existing body")
        from deeptutor.services.creations.drafts import CreationDraftStore

        earlier = _make_draft()
        CreationDraftStore().mark_created(earlier, created_name="my-skill", replaced=False)
        revision = _make_draft(session_id=earlier.session_id)
        response = client.get(f"/api/creations/drafts/{revision.draft_id}")
        assert response.json()["approval"] == "update"


class TestApprove:
    def test_create_writes_a_skill_without_always_true(self, client: TestClient, services) -> None:
        draft = _make_draft(
            content="---\nalways: true\n---\n\n# Body\nDo the thing.",
        )
        response = client.post(f"/api/creations/drafts/{draft.draft_id}/approve", json={})
        assert response.status_code == 200
        body = response.json()
        assert body["already_created"] is False
        assert body["saved_name"] == "my-skill"
        assert body["href"] == "/space/skills"

        detail = services.skills.get_detail("my-skill")
        assert "always" not in detail.content
        assert "Do the thing." in detail.content

    def test_references_land_in_the_skill_dir(self, client: TestClient, services) -> None:
        draft = _make_draft(references=[{"path": "references/notes.md", "content": "extra info"}])
        response = client.post(f"/api/creations/drafts/{draft.draft_id}/approve", json={})
        assert response.status_code == 200
        saved = services.skills.read_skill_file("my-skill", "references/notes.md")
        assert saved == "extra info"

    def test_approve_edits_are_merged(self, client: TestClient, services) -> None:
        draft = _make_draft()
        response = client.post(
            f"/api/creations/drafts/{draft.draft_id}/approve",
            json={"description": "Edited description."},
        )
        assert response.status_code == 200
        assert services.skills.get_detail("my-skill").description == "Edited description."

    def test_conflict_with_another_session_is_409(self, client: TestClient, services) -> None:
        services.skills.create("my-skill", "existing", "existing body")
        draft = _make_draft()
        response = client.post(f"/api/creations/drafts/{draft.draft_id}/approve", json={})
        assert response.status_code == 409

    def test_same_session_revision_updates_instead_of_409(
        self, client: TestClient, services
    ) -> None:
        services.skills.create("my-skill", "first version", "first body")
        from deeptutor.services.creations.drafts import CreationDraftStore

        earlier = _make_draft()
        CreationDraftStore().mark_created(earlier, created_name="my-skill", replaced=False)
        revision = _make_draft(session_id=earlier.session_id, content="# Body\nSecond version.")

        response = client.post(f"/api/creations/drafts/{revision.draft_id}/approve", json={})
        assert response.status_code == 200
        body = response.json()
        assert body["replaced"] is True
        assert "Second version." in services.skills.get_detail("my-skill").content

    def test_repeat_approve_is_idempotent(self, client: TestClient, services) -> None:
        draft = _make_draft()
        first = client.post(f"/api/creations/drafts/{draft.draft_id}/approve", json={})
        assert first.json()["already_created"] is False

        second = client.post(f"/api/creations/drafts/{draft.draft_id}/approve", json={})
        assert second.status_code == 200
        body = second.json()
        assert body["already_created"] is True
        assert body["saved_name"] == "my-skill"

    def test_persona_create_and_update(self, client: TestClient, services) -> None:
        draft = _make_draft(kind="persona", content="You are a patient tutor.")
        response = client.post(f"/api/creations/drafts/{draft.draft_id}/approve", json={})
        assert response.status_code == 200
        assert response.json()["href"] == "/space/personas"
        assert (
            services.personas.get_detail("my-persona")
            .content.strip()
            .endswith("You are a patient tutor.")
        )

    def test_owner_isolation_returns_404_for_another_users_draft(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from deeptutor.multi_user.context import reset_current_user, set_current_user
        from deeptutor.multi_user.models import CurrentUser, UserScope

        draft = _make_draft()

        other_token = set_current_user(
            CurrentUser(
                id="someone-else",
                username="someone-else",
                role="user",
                scope=UserScope(
                    kind="user", user_id="someone-else", root=Path("/tmp/someone-else")
                ),
            )
        )
        try:
            assert client.get(f"/api/creations/drafts/{draft.draft_id}").status_code == 404
            assert (
                client.post(f"/api/creations/drafts/{draft.draft_id}/approve", json={}).status_code
                == 404
            )
        finally:
            reset_current_user(other_token)


# ---------------------------------------------------------------------------
# develop: permission and parity guarantees for the draft entry point.
#
# Drafts are a second way to author a Skill/Persona, so they must not open a
# door the Skills/Personas forms keep shut: the real app mounts the router
# behind ``require_auth``, every draft is readable and approvable only by the
# account that produced it, and an approved draft lands exactly where — and
# looks exactly like — the same artifact created through the form.
# ---------------------------------------------------------------------------


def _enable_real_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    from deeptutor.api.routers import auth as auth_module
    from deeptutor.services import auth as auth_service

    monkeypatch.setattr(auth_module, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_service, "AUTH_SECRET", "test-secret")
    monkeypatch.setattr(auth_service, "POCKETBASE_ENABLED", False)


class TestRealAppMount:
    def test_anonymous_requests_are_rejected_with_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from deeptutor.api import main as api_main

        _enable_real_auth(monkeypatch)
        client = TestClient(api_main.app)
        draft_id = "0" * 32
        assert client.get(f"/api/creations/drafts/{draft_id}").status_code == 401
        assert client.post(f"/api/creations/drafts/{draft_id}/approve", json={}).status_code == 401

    def test_an_ordinary_account_reaches_the_router_like_the_forms(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A learner token is authenticated, not admin-gated: an unknown id is 404."""
        from deeptutor.api import main as api_main
        from deeptutor.multi_user.identity import get_user, save_user
        from deeptutor.services import auth as auth_service

        _enable_real_auth(monkeypatch)
        if get_user("creations-learner") is None:
            save_user(
                "creations-learner", auth_service.hash_password("password123"), role="student"
            )
        token = auth_service.create_token(
            "creations-learner", role="student", user_id=get_user("creations-learner")["id"]
        )
        client = TestClient(api_main.app)
        headers = {"Authorization": f"Bearer {token}"}
        response = client.get("/api/creations/drafts/" + "0" * 32, headers=headers)
        assert response.status_code == 404
        # The router's own answer, not an unmounted path's.
        assert response.json()["detail"] == "Creation draft not found"
        # The same account may use the form it mirrors.
        assert client.get("/api/skills/list", headers=headers).status_code == 200


def _as_user(user_id: str):
    from deeptutor.multi_user.context import set_current_user
    from deeptutor.multi_user.models import CurrentUser, UserScope

    return set_current_user(
        CurrentUser(
            id=user_id,
            username=user_id,
            role="user",
            scope=UserScope(kind="user", user_id=user_id, root=Path(f"/tmp/{user_id}")),
        )
    )


@pytest.fixture
def per_user_services(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Per-account services resolved from the current user, like production.

    The form routes and the draft kinds are pointed at the same resolvers, so
    the tests below prove both write to the same per-account location.
    """
    from deeptutor.multi_user.context import get_current_user

    skills: dict[str, SkillService] = {}
    personas: dict[str, PersonaService] = {}

    def skill_service() -> SkillService:
        uid = get_current_user().id
        return skills.setdefault(
            uid, SkillService(root=tmp_path / uid / "skills", builtin_root=None)
        )

    def persona_service() -> PersonaService:
        uid = get_current_user().id
        return personas.setdefault(uid, PersonaService(root=tmp_path / uid / "personas"))

    admin_service = SimpleNamespace(
        get_workspace_dir=lambda: tmp_path / "admin-personas",
    )
    monkeypatch.setattr(
        drafts_module,
        "get_current_path_service",
        lambda: SimpleNamespace(get_user_root=lambda: tmp_path / get_current_user().id),
    )
    monkeypatch.setattr("deeptutor.services.skill.get_skill_service", skill_service)
    monkeypatch.setattr("deeptutor.services.persona.get_persona_service", persona_service)
    monkeypatch.setattr("deeptutor.api.routers.skills.get_account_skill_service", skill_service)
    monkeypatch.setattr("deeptutor.api.routers.personas.get_persona_service", persona_service)
    monkeypatch.setattr("deeptutor.multi_user.paths.get_admin_path_service", lambda: admin_service)
    monkeypatch.setattr(
        "deeptutor.api.routers.personas.get_admin_path_service", lambda: admin_service
    )
    monkeypatch.setattr("deeptutor.multi_user.skill_access.assigned_skill_infos", lambda _uid: [])
    monkeypatch.setattr("deeptutor.api.routers.skills.assigned_skill_infos", lambda _uid: [])
    return SimpleNamespace(
        skill_service=skill_service,
        persona_service=persona_service,
        admin_personas_root=tmp_path / "admin-personas" / "personas",
    )


@pytest.fixture
def multi_client(per_user_services, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from deeptutor.api.routers import auth as auth_module
    from deeptutor.api.routers import personas as personas_router_mod
    from deeptutor.api.routers import skills as skills_router_mod
    import deeptutor.api.routers.creations as creations_router_mod

    monkeypatch.setattr(auth_module, "AUTH_ENABLED", False)
    creations_router_mod._approve_locks.clear()
    app = FastAPI()
    app.include_router(creations_router_mod.router, prefix="/api/creations")
    app.include_router(skills_router_mod.router, prefix="/api/skills")
    app.include_router(personas_router_mod.router, prefix="/api")
    return TestClient(app)


class TestCrossAccountIsolation:
    def test_another_account_cannot_read_or_approve_a_draft(
        self, multi_client: TestClient, per_user_services
    ) -> None:
        from deeptutor.multi_user.context import reset_current_user

        token = _as_user("owner-a")
        try:
            draft = _make_draft()
        finally:
            reset_current_user(token)

        token = _as_user("intruder-b")
        try:
            assert multi_client.get(f"/api/creations/drafts/{draft.draft_id}").status_code == 404
            response = multi_client.post(
                f"/api/creations/drafts/{draft.draft_id}/approve",
                json={"name": "stolen-skill"},
            )
            assert response.status_code == 404
            assert per_user_services.skill_service().list_skills() == []
        finally:
            reset_current_user(token)

        token = _as_user("owner-a")
        try:
            assert per_user_services.skill_service().list_skills() == []
            # The owner can still approve it afterwards.
            response = multi_client.post(f"/api/creations/drafts/{draft.draft_id}/approve", json={})
            assert response.status_code == 200
        finally:
            reset_current_user(token)

    def test_a_draft_id_planted_in_another_accounts_store_is_not_honoured(
        self, multi_client: TestClient, per_user_services, tmp_path: Path
    ) -> None:
        """Even a copied draft file keeps its owner: owner_id is checked on read."""
        import shutil

        from deeptutor.multi_user.context import reset_current_user

        token = _as_user("owner-a")
        try:
            draft = _make_draft()
        finally:
            reset_current_user(token)
        target = tmp_path / "intruder-b" / "creation_drafts"
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy(
            tmp_path / "owner-a" / "creation_drafts" / f"{draft.draft_id}.json",
            target / f"{draft.draft_id}.json",
        )

        token = _as_user("intruder-b")
        try:
            assert multi_client.get(f"/api/creations/drafts/{draft.draft_id}").status_code == 404
            assert (
                multi_client.post(
                    f"/api/creations/drafts/{draft.draft_id}/approve", json={}
                ).status_code
                == 404
            )
            assert per_user_services.skill_service().list_skills() == []
        finally:
            reset_current_user(token)

    def test_approve_writes_only_into_the_approving_accounts_layer(
        self, multi_client: TestClient, per_user_services
    ) -> None:
        from deeptutor.multi_user.context import reset_current_user

        token = _as_user("other-b")
        try:
            per_user_services.skill_service().create("my-skill", "B's own skill", "B body")
        finally:
            reset_current_user(token)

        token = _as_user("owner-a")
        try:
            draft = _make_draft()
            response = multi_client.post(f"/api/creations/drafts/{draft.draft_id}/approve", json={})
            # Another account using the same name is not a conflict for A ...
            assert response.status_code == 200
            assert per_user_services.skill_service().get_detail("my-skill").description == (
                "A test artifact."
            )
        finally:
            reset_current_user(token)

        token = _as_user("other-b")
        try:
            # ... and B's skill is untouched.
            detail = per_user_services.skill_service().get_detail("my-skill")
            assert detail.description == "B's own skill"
            assert "B body" in detail.content
        finally:
            reset_current_user(token)

    def test_another_session_of_the_same_account_cannot_overwrite_by_name(
        self, multi_client: TestClient, per_user_services
    ) -> None:
        """Session binding: only the session that created a name may update it."""
        from deeptutor.multi_user.context import reset_current_user

        token = _as_user("owner-a")
        try:
            first = _make_draft(session_id="session-1")
            assert (
                multi_client.post(
                    f"/api/creations/drafts/{first.draft_id}/approve", json={}
                ).status_code
                == 200
            )
            second = _make_draft(session_id="session-2", content="# Body\nOverwrite attempt.")
            response = multi_client.post(
                f"/api/creations/drafts/{second.draft_id}/approve", json={}
            )
            assert response.status_code == 409
            assert "Overwrite attempt." not in (
                per_user_services.skill_service().get_detail("my-skill").content
            )
        finally:
            reset_current_user(token)


class TestParityWithForms:
    def test_skill_from_a_draft_matches_the_form_created_skill(
        self, multi_client: TestClient, per_user_services
    ) -> None:
        from deeptutor.multi_user.context import reset_current_user

        token = _as_user("owner-a")
        try:
            form = multi_client.post(
                "/api/skills/create",
                json={
                    "name": "form-skill",
                    "description": "Explains limits step by step.",
                    "content": "# Limits\n\n1. Read the question.\n2. Simplify.",
                    "tags": ["math"],
                },
            )
            assert form.status_code == 200
            draft = _make_draft(
                name="draft-skill",
                description="Explains limits step by step.",
                content="# Limits\n\n1. Read the question.\n2. Simplify.",
                tags=["math"],
            )
            approved = multi_client.post(f"/api/creations/drafts/{draft.draft_id}/approve", json={})
            assert approved.status_code == 200

            form_detail = multi_client.get("/api/skills/form-skill").json()
            draft_detail = multi_client.get("/api/skills/draft-skill").json()
            assert set(draft_detail) == set(form_detail)
            for key in set(form_detail) - {"name", "content"}:
                assert draft_detail[key] == form_detail[key], key
            assert (
                draft_detail["content"].replace("draft-skill", "form-skill")
                == (form_detail["content"])
            )
            listed = {
                item["name"] for item in multi_client.get("/api/skills/list").json()["skills"]
            }
            assert {"form-skill", "draft-skill"} <= listed
        finally:
            reset_current_user(token)

    def test_persona_from_a_draft_matches_the_form_created_persona(
        self, multi_client: TestClient, per_user_services
    ) -> None:
        from deeptutor.multi_user.context import reset_current_user

        token = _as_user("owner-a")
        try:
            form = multi_client.post(
                "/api/personas",
                json={
                    "name": "form-persona",
                    "description": "A patient tutor.",
                    "content": "You are a patient tutor.",
                },
            )
            assert form.status_code == 200
            draft = _make_draft(
                kind="persona",
                name="draft-persona",
                description="A patient tutor.",
                content="You are a patient tutor.",
            )
            approved = multi_client.post(f"/api/creations/drafts/{draft.draft_id}/approve", json={})
            assert approved.status_code == 200

            form_detail = multi_client.get("/api/personas/form-persona").json()
            draft_detail = multi_client.get("/api/personas/draft-persona").json()
            assert set(draft_detail) == set(form_detail)
            for key in set(form_detail) - {"name", "content"}:
                assert draft_detail[key] == form_detail[key], key
            assert (
                draft_detail["content"].replace("draft-persona", "form-persona")
                == (form_detail["content"])
            )
        finally:
            reset_current_user(token)

    def test_a_draft_cannot_shadow_an_admin_preset_persona(
        self, multi_client: TestClient, per_user_services
    ) -> None:
        """Admin presets are read-only overlays: a draft must not take one's name."""
        from deeptutor.multi_user.context import reset_current_user

        PersonaService(root=per_user_services.admin_personas_root).create(
            "teacher-preset", "Admin preset", "You are the deployment preset."
        )
        token = _as_user("owner-a")
        try:
            draft = _make_draft(kind="persona", name="teacher-preset")
            response = multi_client.post(f"/api/creations/drafts/{draft.draft_id}/approve", json={})
            assert response.status_code == 409
        finally:
            reset_current_user(token)

    def test_approve_ignores_fields_the_form_does_not_offer(
        self, multi_client: TestClient, per_user_services
    ) -> None:
        """No admin-only frontmatter (``always``/``requires``) can be smuggled in."""
        from deeptutor.multi_user.context import reset_current_user

        token = _as_user("owner-a")
        try:
            draft = _make_draft()
            response = multi_client.post(
                f"/api/creations/drafts/{draft.draft_id}/approve",
                json={"always": True, "requires": {"env": ["SECRET"]}, "kind": "persona"},
            )
            assert response.status_code == 200
            text = per_user_services.skill_service().get_detail("my-skill").content
            assert "always" not in text
            assert "requires" not in text
            assert per_user_services.persona_service().list_personas() == []
        finally:
            reset_current_user(token)
