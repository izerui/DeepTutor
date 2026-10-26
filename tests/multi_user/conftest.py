"""Shared fixtures for the multi_user test suite.

These fixtures isolate each test under ``tmp_path`` so we never read or write
the developer's real ``data/`` or ``multi-user/`` directories. They also
provide a context manager that pushes a ``CurrentUser`` onto the contextvar
for tests that need to call user-scoped code without going through HTTP.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from deeptutor.multi_user.context import reset_current_user, set_current_user
from deeptutor.multi_user.models import CurrentUser, UserScope


@pytest.fixture
def mu_isolated_root(tmp_path, monkeypatch) -> Path:
    """Redirect every ``multi_user`` global path under ``tmp_path``.

    Also clears the ``_path_services`` cache so ``get_path_service()`` can be
    re-resolved per test without leaking instances created in earlier tests.
    """
    from deeptutor.multi_user import (
        audit,
        device_credentials,
        grants,
        guardians,
        identity,
        paths,
    )

    project_root = tmp_path
    admin_root = (project_root / "data").resolve()
    users_root = admin_root / "users"
    system_root = admin_root / "system"

    monkeypatch.setattr(paths, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(paths, "USERS_ROOT", users_root)
    monkeypatch.setattr(paths, "SYSTEM_ROOT", system_root)
    monkeypatch.setattr(paths, "ADMIN_WORKSPACE_ROOT", admin_root)
    monkeypatch.setattr(paths, "LEGACY_MULTI_USER_ROOT", project_root / "multi-user")
    monkeypatch.setattr(paths, "_path_services", {})

    monkeypatch.setattr(identity, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(identity, "SYSTEM_ROOT", system_root)
    monkeypatch.setattr(identity, "AUTH_DIR", system_root / "auth")
    monkeypatch.setattr(identity, "USERS_FILE", system_root / "auth" / "users.json")
    monkeypatch.setattr(identity, "SECRET_FILE", system_root / "auth" / "auth_secret")
    monkeypatch.setattr(
        identity,
        "LEGACY_USERS_FILE",
        project_root / "data" / "user" / "auth_users.json",
    )
    monkeypatch.setattr(
        identity,
        "LEGACY_SECRET_FILE",
        project_root / "data" / "user" / "auth_secret",
    )

    monkeypatch.setattr(grants, "GRANTS_DIR", system_root / "grants")
    monkeypatch.setattr(guardians, "GUARDIANS_FILE", system_root / "guardians.json")
    monkeypatch.setattr(audit, "SYSTEM_ROOT", system_root)
    monkeypatch.setattr(
        device_credentials,
        "DEVICE_CREDENTIALS_FILE",
        system_root / "auth" / "device_credentials.json",
    )

    # The ``auth.json`` bootstrap admin is process-global state rather than a
    # path, and it now takes part in the first-user promotion decision (#849).
    # Clear it so a developer with real credentials configured sees the same
    # results as CI; tests that need one patch these back explicitly.
    from deeptutor.services import auth as auth_service

    monkeypatch.setattr(auth_service, "AUTH_USERNAME", "")
    monkeypatch.setattr(auth_service, "AUTH_PASSWORD_HASH", "")

    admin_root.mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture
def make_user(mu_isolated_root):
    """Build a ``CurrentUser`` rooted under the isolated tmp_path."""

    def _make(uid: str, *, role: str = "user", username: str | None = None) -> CurrentUser:
        from deeptutor.multi_user.paths import admin_scope

        if role == "admin":
            scope = admin_scope()
        else:
            scope = UserScope(
                kind="user",
                user_id=uid,
                root=(mu_isolated_root / "data" / "users" / uid).resolve(),
            )
        return CurrentUser(
            id=uid,
            username=username or uid,
            role=role,
            scope=scope,
        )

    return _make


@pytest.fixture
def as_user(make_user):
    """Context manager that pushes a CurrentUser onto the contextvar.

    Usage:
        with as_user("u_alice", role="user"):
            ...
    """

    @contextmanager
    def _scope(uid: str, *, role: str = "user", username: str | None = None):
        token = set_current_user(make_user(uid, role=role, username=username))
        try:
            yield
        finally:
            reset_current_user(token)

    return _scope


@pytest.fixture
def seed_user(mu_isolated_root):
    """Create a user record on disk and return the resulting record dict."""

    def _seed(username: str, password: str = "password1234", role: str = "user") -> dict:
        from deeptutor.multi_user.identity import save_user
        from deeptutor.services.auth import hash_password

        return save_user(username, hash_password(password), role=role)  # type: ignore[arg-type]

    return _seed


@pytest.fixture
def make_kb():
    """Write a minimal knowledge base into ``manager``'s catalog.

    Ordinary KBs get a ``raw/doc.md`` so the manager's orphan prune keeps them;
    connected entries (an ``entry`` with a ``type``) get no folder, like the
    real connect routes.
    """

    def _make(manager, name: str, entry: dict | None = None, *, text: str = "hello"):
        folder = Path(manager.base_dir) / name
        if entry is None or "type" not in entry:
            (folder / "raw").mkdir(parents=True, exist_ok=True)
            (folder / "raw" / "doc.md").write_text(text, encoding="utf-8")
        manager.config = manager._load_config()
        manager.config.setdefault("knowledge_bases", {})[name] = entry or {
            "path": name,
            "rag_provider": "llamaindex",
            "status": "ready",
            "description": name,
        }
        manager._save_config()
        return folder

    return _make


@pytest.fixture
def fake_kb_registry(monkeypatch, tmp_path):
    """Plan B's knowledge-base registry, reduced to what teaching reads.

    Records are keyed by ``kb_id``; ``legacy_refs`` stand in for the old
    ``user:kb:<name>`` style references a teacher's draft still carries.
    ``references`` records what teaching registered, keyed by
    ``(referrer_kind, referrer_id)``; ``documents`` mirrors plan B's document
    table, keyed by ``(kb_id, rel_path)``. Kinds follow plan B: ``"uploaded"``
    for ordinary KBs, ``registry.CONNECTOR_KINDS`` for connectors.
    """
    from types import SimpleNamespace

    from deeptutor.knowledge import registry

    root = tmp_path / "fake_kbs"
    records: dict[str, SimpleNamespace] = {}
    aliases: dict[str, str] = {}
    references: dict[tuple[str, str], list] = {}
    documents: dict[tuple[str, str], SimpleNamespace] = {}
    #: (referrer_kind, referrer_id) -> plan B ``RevokedReference``-shaped rows.
    revoked: dict[tuple[str, str], list] = {}

    def _strip(kb_id: str) -> str:
        return kb_id[3:] if str(kb_id).startswith("kb:") else str(kb_id)

    def add(
        kb_id: str,
        *,
        visibility: str = "public",
        created_by: str = "",
        kind: str = registry.UPLOADED_KIND,
        ready: bool = True,
        name: str = "",
        legacy_refs: tuple[str, ...] = (),
    ) -> SimpleNamespace:
        record = SimpleNamespace(
            kb_id=kb_id,
            name=name or kb_id,
            base_dir=str(root),
            dir_name=kb_id,
            kind=kind,
            visibility=visibility,
            created_by=created_by,
            ready=ready,
            ref=f"kb:{kb_id}",
        )
        records[kb_id] = record
        for ref in legacy_refs:
            aliases[ref] = kb_id
        return record

    def write_file(kb_id: str, rel_path: str, text: str = "content"):
        path = root / kb_id / "raw" / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def resolve_ref(ref):
        ref = str(ref or "")
        kb_id = ref[3:] if ref.startswith("kb:") else aliases.get(ref, "")
        return records.get(kb_id)

    def can_read(record, user):
        if record.visibility == "public" or record.created_by == user.id:
            return True
        if not references:
            # Nothing cites it, so no template can grant access (and Task 11's
            # ``teaching.access`` need not exist yet).
            return False
        from deeptutor.teaching.access import template_reader

        return any(
            kind == "mastery_template"
            and any(kb_id == record.kb_id for kb_id, _docs in refs)
            and template_reader(referrer_id, user.id)
            for (kind, referrer_id), refs in references.items()
        )

    def set_visibility(kb_id, visibility, *, force=False):
        records[_strip(kb_id)].visibility = visibility
        return records[_strip(kb_id)]

    def replace_references(kind, referrer_id, refs, *, created_by, **_options):
        references[(kind, referrer_id)] = list(refs)

    def find_document(kb_id, rel_path):
        return documents.get((_strip(kb_id), rel_path))

    def upsert_documents(kb_id, hashes):
        kb_id = _strip(kb_id)
        current = []
        for rel_path, content_hash in hashes.items():
            previous = documents.get((kb_id, rel_path))
            if previous is None:
                doc = SimpleNamespace(
                    kb_id=kb_id,
                    doc_id=f"doc_{len(documents) + 1}",
                    rel_path=rel_path,
                    content_hash=content_hash,
                    version=1,
                    deleted=False,
                )
            elif previous.content_hash != content_hash or previous.deleted:
                doc = SimpleNamespace(
                    **{
                        **vars(previous),
                        "content_hash": content_hash,
                        "version": previous.version + 1,
                        "deleted": False,
                    }
                )
            else:
                doc = previous
            documents[(kb_id, rel_path)] = doc
            current.append(doc)
        return current

    monkeypatch.setattr(registry, "resolve_ref", resolve_ref)
    monkeypatch.setattr(registry, "get_kb", lambda kb_id: records.get(_strip(kb_id)))
    monkeypatch.setattr(
        registry, "is_connector", lambda record: record.kind in registry.CONNECTOR_KINDS
    )
    monkeypatch.setattr(
        registry,
        "can_write",
        lambda record, user: user.is_admin or record.created_by == user.id,
    )
    monkeypatch.setattr(registry, "can_read", can_read)
    monkeypatch.setattr(registry, "set_visibility", set_visibility)
    monkeypatch.setattr(registry, "replace_references", replace_references)
    monkeypatch.setattr(
        registry,
        "remove_references",
        lambda kind, referrer_id: references.pop((kind, referrer_id), None),
    )
    monkeypatch.setattr(registry, "find_document", find_document)
    monkeypatch.setattr(registry, "upsert_documents", upsert_documents)
    monkeypatch.setattr(
        registry,
        "list_unavailable_references",
        lambda kind, referrer_id: list(revoked.get((kind, referrer_id), [])),
    )
    monkeypatch.setattr("deeptutor.teaching.kb.kb_ready", lambda record: record.ready)

    def force_private(kb_id: str) -> None:
        """What plan B does when an admin forces a referenced KB private."""
        records[kb_id].visibility = "private"
        for (kind, referrer_id), refs in list(references.items()):
            if any(ref_kb == kb_id for ref_kb, _docs in refs):
                references[(kind, referrer_id)] = [r for r in refs if r[0] != kb_id]
                revoked.setdefault((kind, referrer_id), []).append(
                    SimpleNamespace(
                        kb_id=kb_id,
                        kb_name=records[kb_id].name,
                        referrer_kind=kind,
                        referrer_id=referrer_id,
                        doc_ids=None,
                        created_by="",
                        reason="made_private",
                        revoked_at="",
                    )
                )

    return SimpleNamespace(
        add=add,
        write_file=write_file,
        force_private=force_private,
        records=records,
        references=references,
        documents=documents,
        revoked=revoked,
    )


@pytest.fixture
def teaching_users(seed_user):
    """One account per teaching role, keyed by username.

    The admin is created first on purpose: ``save_user`` promotes the very
    first account to admin whatever role it was asked for.
    """
    accounts = (
        ("adam", "admin"),
        ("tina", "teacher"),
        ("tom", "teacher"),
        ("sara", "student"),
        ("sam", "student"),
        ("uma", "user"),
    )
    return {name: seed_user(name, role=role) for name, role in accounts}


@pytest.fixture
def as_account(as_user, teaching_users):
    """``with as_account("sara"):`` — act as one of ``teaching_users``."""

    def _enter(name: str):
        user = teaching_users[name]
        return as_user(user["id"], role=user["role"], username=name)

    return _enter


@pytest.fixture
def teaching_books(teaching_users, tmp_path):
    """Real plan B / plan D registrations (isolated per test by their autouse fixtures).

    Tina owns a private book "Waves" citing her private KB "answers"; Tom owns
    a public book "Optics" and a private book "Tom's notes".
    """
    from types import SimpleNamespace

    from deeptutor.book import registry as book_registry
    from deeptutor.knowledge import registry as kb_registry

    tina = teaching_users["tina"]["id"]
    tom = teaching_users["tom"]["id"]
    answers = kb_registry.register_kb(
        base_dir=tmp_path / "kbs", name="answers", kind="uploaded", created_by=tina
    )
    own = book_registry.register_book(
        base_dir=tmp_path / "books", dir_name="book_bk_own", title="Waves", created_by=tina
    )
    book_registry.set_book_kb_links(own.book_id, {answers.ref: answers.kb_id})
    public = book_registry.register_book(
        base_dir=tmp_path / "books",
        dir_name="book_bk_pub",
        title="Optics",
        created_by=tom,
        visibility="public",
    )
    foreign = book_registry.register_book(
        base_dir=tmp_path / "books", dir_name="book_bk_tom", title="Tom's notes", created_by=tom
    )
    return SimpleNamespace(answers=answers, own=own, public=public, foreign=foreign)
