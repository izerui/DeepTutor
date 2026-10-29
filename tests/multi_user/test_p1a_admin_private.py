"""P1a / S10: an administrator cannot read or write another account's private items."""

from __future__ import annotations

from deeptutor.book import registry as books
from deeptutor.knowledge import registry as kbs
from deeptutor.multi_user.models import LOCAL_ADMIN_ID


def _kb(tmp_path, owner, name, visibility):
    return kbs.register_kb(
        base_dir=tmp_path / "kbs", name=name, kind="uploaded",
        created_by=owner, visibility=visibility,
    )


def _book(tmp_path, owner, name, visibility):
    return books.register_book(
        base_dir=tmp_path / "books", dir_name=f"book_{name}", title=name,
        created_by=owner, visibility=visibility,
    )


def test_admin_cannot_open_a_teachers_private_kb(make_user, tmp_path):
    admin = make_user("boss", role="admin")
    private = _kb(tmp_path, "tina", "notes", "private")
    assert not kbs.can_read(private, admin)
    assert not kbs.can_write(private, admin)


def test_admin_keeps_own_kbs_and_the_published_fallback(make_user, tmp_path):
    admin = make_user("boss", role="admin")
    shared_identity = _kb(tmp_path, LOCAL_ADMIN_ID, "shared-admin", "private")
    real_identity = _kb(tmp_path, "boss", "mine", "private")
    published = _kb(tmp_path, "tina", "physics", "public")
    for record in (shared_identity, real_identity, published):
        assert kbs.can_read(record, admin)
    for record in (shared_identity, real_identity):
        assert kbs.can_write(record, admin)
    # spec 2026-10-29 D10 (P4): someone else's published KB is only withdrawn or deleted.
    assert not kbs.can_write(published, admin)
    from deeptutor.publishing.admin import admin_retiring

    with admin_retiring():
        assert kbs.can_write(published, admin)


def test_owner_and_strangers_are_unchanged_for_kbs(make_user, tmp_path):
    teacher = make_user("tina", role="teacher")
    student = make_user("sam", role="student")
    private = _kb(tmp_path, "tina", "notes", "private")
    assert kbs.can_read(private, teacher) and kbs.can_write(private, teacher)
    assert not kbs.can_read(private, student) and not kbs.can_write(private, student)


def test_admin_cannot_open_a_teachers_private_book(make_user, tmp_path):
    admin = make_user("boss", role="admin")
    private = _book(tmp_path, "tina", "draft", "private")
    assert not books.can_read_book(private, admin)
    assert not books.can_write_book(private, admin)


def test_admin_keeps_own_books_and_the_published_fallback(make_user, tmp_path):
    admin = make_user("boss", role="admin")
    published = _book(tmp_path, "tina", "physics", "public")
    for record in (
        _book(tmp_path, LOCAL_ADMIN_ID, "shared-admin", "private"),
        _book(tmp_path, "boss", "mine", "private"),
        published,
    ):
        assert books.can_read_book(record, admin)
        assert books.can_write_book(record, admin) is (record is not published)
    # spec 2026-10-29 D10 (P4): someone else's published book is only withdrawn or deleted.
    from deeptutor.publishing.admin import admin_retiring

    with admin_retiring():
        assert books.can_write_book(published, admin)


def test_system_code_without_a_request_user_keeps_full_access(mu_isolated_root, tmp_path):
    from deeptutor.multi_user.paths import local_admin_user

    private_kb = _kb(tmp_path, "tina", "notes", "private")
    private_book = _book(tmp_path, "tina", "draft", "private")
    system = local_admin_user()
    assert kbs.can_read(private_kb, system) and kbs.can_write(private_kb, system)
    assert books.can_read_book(private_book, system) and books.can_write_book(private_book, system)


def test_a_signed_in_local_admin_is_not_the_system(as_user, mu_isolated_root, tmp_path):
    from deeptutor.multi_user.context import get_current_user

    private_kb = _kb(tmp_path, "tina", "notes", "private")
    with as_user(LOCAL_ADMIN_ID, role="admin"):
        assert not kbs.can_read(private_kb, get_current_user())
