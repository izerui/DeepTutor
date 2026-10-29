"""Class management (spec 2026-10-29 §5, D11, D18-D20).

Administrators manage every class; teachers manage the classes they teach.
Students join through a class invite link, by accepting a teacher's
invitation, or by an administrator adding them; they can leave on their own.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from typing import Any
import uuid

from deeptutor.classes import store
from deeptutor.multi_user.context import get_current_user
from deeptutor.multi_user.models import CurrentUser

logger = logging.getLogger(__name__)

NAME_MAX = 100
INVITE_TTL_MAX_DAYS = 365


class ClassError(Exception):
    status = 400


class ClassNotFound(ClassError):
    status = 404


class ClassForbidden(ClassError):
    status = 403


class ClassConflict(ClassError):
    status = 409


def _accounts() -> dict[str, dict[str, Any]]:
    """Account id -> {username, role, disabled} from the live user store."""
    from deeptutor.multi_user.identity import load_users
    from deeptutor.multi_user.models import normalize_role

    accounts: dict[str, dict[str, Any]] = {}
    for username, record in load_users().items():
        user_id = str(record.get("id") or "")
        if user_id:
            accounts[user_id] = {
                "username": username,
                "role": normalize_role(str(record.get("role") or "")),
                "disabled": bool(record.get("disabled", False)),
            }
    return accounts


def _role(user: CurrentUser) -> str:
    """The actor's live role (a role change takes effect before the token expires)."""
    if user.is_admin:
        return "admin"
    account = _accounts().get(user.id)
    return account["role"] if account else user.role


def _now() -> float:
    return time.time()


def _clean_name(name: str) -> str:
    name = str(name or "").strip()
    if not name or len(name) > NAME_MAX:
        raise ClassError(f"A class name must contain 1 to {NAME_MAX} characters.")
    return name


class ClassService:
    def __init__(self, actor: CurrentUser | None = None) -> None:
        self.actor = actor or get_current_user()

    # ── authorization ─────────────────────────────────────────

    def _is_admin(self) -> bool:
        return self.actor.is_admin

    def _require_teacher_or_admin(self) -> None:
        if not self._is_admin() and _role(self.actor) != "teacher":
            raise ClassForbidden("Only teachers and administrators can manage classes.")

    def _class_row(self, conn, class_id: str):
        row = conn.execute("SELECT * FROM classes WHERE id = ?", (class_id,)).fetchone()
        if row is None:
            raise ClassNotFound("Class not found.")
        return row

    def _teaches(self, conn, class_id: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM class_teachers WHERE class_id = ? AND user_id = ?",
            (class_id, self.actor.id),
        ).fetchone() is not None

    def _require_manager(self, conn, class_id: str):
        row = self._class_row(conn, class_id)
        # The live role counts: a teacher demoted to student manages nothing,
        # even before their old class rows are cleaned up.
        if not self._is_admin() and (_role(self.actor) != "teacher" or not self._teaches(conn, class_id)):
            raise ClassForbidden("Only this class's teachers and administrators can manage it.")
        return row

    # ── classes ───────────────────────────────────────────────

    def create(self, name: str) -> dict[str, Any]:
        self._require_teacher_or_admin()
        name = _clean_name(name)
        class_id = f"cls_{uuid.uuid4().hex}"
        now = _now()
        with store.write() as conn:
            conn.execute(
                "INSERT INTO classes (id, name, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (class_id, name, self.actor.id, now, now),
            )
            if not self._is_admin():
                conn.execute(
                    "INSERT INTO class_teachers VALUES (?, ?, ?, ?)",
                    (class_id, self.actor.id, self.actor.id, now),
                )
        return self.get(class_id)

    def update(self, class_id: str, *, name: str | None = None, archived: bool | None = None) -> dict[str, Any]:
        with store.write() as conn:
            self._require_manager(conn, class_id)
            if name is not None:
                conn.execute(
                    "UPDATE classes SET name = ?, updated_at = ? WHERE id = ?",
                    (_clean_name(name), _now(), class_id),
                )
            if archived is not None:
                conn.execute(
                    "UPDATE classes SET archived = ?, updated_at = ? WHERE id = ?",
                    (1 if archived else 0, _now(), class_id),
                )
                if archived:
                    # An archived class admits nobody: restoring it later must
                    # not revive an old link or old invitations.
                    conn.execute("DELETE FROM class_invite_links WHERE class_id = ?", (class_id,))
                    conn.execute(
                        "UPDATE class_invitations SET status = 'cancelled', responded_at = ? "
                        "WHERE class_id = ? AND status = 'pending'",
                        (_now(), class_id),
                    )
            members = [
                r["student_id"]
                for r in conn.execute("SELECT student_id FROM class_members WHERE class_id = ?", (class_id,))
            ] if archived is not None else []
        # Archiving ends what reached students through this class; restoring returns it.
        _paths_follow(*members)
        return self.get(class_id)

    def list_classes(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        with store.read() as conn:
            if conn is None:
                return []
            if self._is_admin():
                rows = conn.execute("SELECT * FROM classes ORDER BY created_at DESC").fetchall()
            elif _role(self.actor) == "teacher":
                rows = conn.execute(
                    "SELECT c.* FROM classes c JOIN class_teachers t ON t.class_id = c.id "
                    "WHERE t.user_id = ? ORDER BY c.created_at DESC",
                    (self.actor.id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT c.* FROM classes c JOIN class_members m ON m.class_id = c.id "
                    "WHERE m.student_id = ? ORDER BY c.created_at DESC",
                    (self.actor.id,),
                ).fetchall()
            counts = {
                row["class_id"]: row["n"]
                for row in conn.execute(
                    "SELECT class_id, COUNT(*) AS n FROM class_members GROUP BY class_id"
                ).fetchall()
            }
        return [
            {**self._summary(row), "student_count": counts.get(row["id"], 0)}
            for row in rows
            if include_archived or not row["archived"]
        ]

    def get(self, class_id: str) -> dict[str, Any]:
        accounts = _accounts()
        with store.read() as conn:
            if conn is None:
                raise ClassNotFound("Class not found.")
            row = self._class_row(conn, class_id)
            manager = self._is_admin() or self._teaches(conn, class_id)
            member = conn.execute(
                "SELECT 1 FROM class_members WHERE class_id = ? AND student_id = ?",
                (class_id, self.actor.id),
            ).fetchone() is not None
            if not manager and not member:
                raise ClassNotFound("Class not found.")
            teachers = conn.execute(
                "SELECT user_id FROM class_teachers WHERE class_id = ? ORDER BY added_at", (class_id,)
            ).fetchall()
            result = {
                **self._summary(row),
                "can_manage": manager,
                "teachers": [self._person(accounts, t["user_id"]) for t in teachers],
            }
            if manager:
                result["student_policy"] = _class_policy(row)
                members = conn.execute(
                    "SELECT student_id, joined_via, joined_at FROM class_members "
                    "WHERE class_id = ? ORDER BY joined_at",
                    (class_id,),
                ).fetchall()
                result["students"] = [
                    {**self._person(accounts, m["student_id"]), "joined_via": m["joined_via"],
                     "joined_at": m["joined_at"]}
                    for m in members
                ]
                pending = conn.execute(
                    "SELECT id, student_id, created_at FROM class_invitations "
                    "WHERE class_id = ? AND status = 'pending' ORDER BY created_at",
                    (class_id,),
                ).fetchall()
                result["pending_invitations"] = [
                    {"id": p["id"], **self._person(accounts, p["student_id"]), "created_at": p["created_at"]}
                    for p in pending
                ]
        return result

    @staticmethod
    def _summary(row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "archived": bool(row["archived"]),
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _person(accounts: dict[str, dict[str, Any]], user_id: str) -> dict[str, Any]:
        account = accounts.get(user_id) or {}
        return {"id": user_id, "username": account.get("username") or user_id}

    def set_student_policy(self, class_id: str, policy: dict[str, Any]) -> dict[str, Any]:
        """Class-wide student permissions; administrators only (spec §6).

        Each key is ``True`` (allow), ``False`` (do not allow) or absent / ``None``
        (inherit the default).
        """
        from deeptutor.multi_user.roles import STUDENT_POLICY_DEFAULTS

        if not self._is_admin():
            raise ClassForbidden("Only administrators set student permissions.")
        cleaned = {
            key: value for key, value in (policy or {}).items()
            if key in STUDENT_POLICY_DEFAULTS and isinstance(value, bool)
        }
        with store.write() as conn:
            self._require_manager(conn, class_id)
            conn.execute(
                "UPDATE classes SET student_policy_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(cleaned, sort_keys=True), _now(), class_id),
            )
        return self.get(class_id)

    # ── teachers ──────────────────────────────────────────────

    def add_teacher(self, class_id: str, user_id: str) -> dict[str, Any]:
        account = _accounts().get(user_id)
        if account is None or account["role"] != "teacher" or account["disabled"]:
            raise ClassError("Only an active teacher account can teach a class.")
        with store.write() as conn:
            self._require_manager(conn, class_id)
            conn.execute(
                "INSERT OR IGNORE INTO class_teachers VALUES (?, ?, ?, ?)",
                (class_id, user_id, self.actor.id, _now()),
            )
        return self.get(class_id)

    def remove_teacher(self, class_id: str, user_id: str) -> dict[str, Any]:
        with store.write() as conn:
            self._require_manager(conn, class_id)
            if not self._is_admin():
                # Spec §5: administrators change who teaches; a teacher may only step down,
                # and never leave the class without a teacher.
                if user_id != self.actor.id:
                    raise ClassForbidden("Only administrators remove other teachers.")
                remaining = conn.execute(
                    "SELECT COUNT(*) FROM class_teachers WHERE class_id = ? AND user_id != ?",
                    (class_id, user_id),
                ).fetchone()[0]
                if not remaining:
                    raise ClassConflict("Add another teacher before leaving this class.")
            conn.execute(
                "DELETE FROM class_teachers WHERE class_id = ? AND user_id = ?", (class_id, user_id)
            )
        return self.get(class_id) if self._is_admin() or user_id != self.actor.id else {"id": class_id}

    # ── students ──────────────────────────────────────────────

    @staticmethod
    def _require_student(accounts: dict[str, dict[str, Any]], user_id: str) -> None:
        account = accounts.get(user_id)
        if account is None or account["role"] != "student" or account["disabled"]:
            raise ClassError("Only an active student account can join a class.")

    def add_students(self, class_id: str, student_ids: list[str]) -> dict[str, Any]:
        """Administrators place students directly (spec §5, way 3)."""
        if not self._is_admin():
            raise ClassForbidden("Only administrators add students directly; teachers send invitations.")
        accounts = _accounts()
        ids = list(dict.fromkeys(str(s).strip() for s in student_ids if str(s).strip()))
        for student_id in ids:
            self._require_student(accounts, student_id)
        with store.write() as conn:
            if self._require_manager(conn, class_id)["archived"]:
                raise ClassConflict("This class is archived.")
            for student_id in ids:
                self._join(conn, class_id, student_id, via="admin")
        _paths_follow(*ids)
        return self.get(class_id)

    def remove_student(self, class_id: str, student_id: str) -> dict[str, Any]:
        with store.write() as conn:
            self._require_manager(conn, class_id)
            conn.execute(
                "DELETE FROM class_members WHERE class_id = ? AND student_id = ?", (class_id, student_id)
            )
        _paths_follow(student_id)
        return self.get(class_id)

    def leave(self, class_id: str) -> None:
        with store.write() as conn:
            self._class_row(conn, class_id)
            deleted = conn.execute(
                "DELETE FROM class_members WHERE class_id = ? AND student_id = ?",
                (class_id, self.actor.id),
            ).rowcount
        if not deleted:
            raise ClassNotFound("You are not in this class.")
        _paths_follow(self.actor.id)

    def _join(self, conn, class_id: str, student_id: str, *, via: str) -> None:
        conn.execute(
            "INSERT OR IGNORE INTO class_members VALUES (?, ?, ?, ?, ?)",
            (class_id, student_id, via, self.actor.id, _now()),
        )
        # Joining by any route settles a pending invitation to the same class.
        conn.execute(
            "UPDATE class_invitations SET status = 'accepted', responded_at = ? "
            "WHERE class_id = ? AND student_id = ? AND status = 'pending'",
            (_now(), class_id, student_id),
        )

    # ── teacher invitations ───────────────────────────────────

    def search_students(self, query: str) -> list[dict[str, Any]]:
        """Usernames only, for sending invitations (spec §5)."""
        self._require_teacher_or_admin()
        needle = str(query or "").strip().casefold()
        if not needle:
            return []
        return [
            {"id": user_id, "username": account["username"]}
            for user_id, account in sorted(_accounts().items(), key=lambda item: item[1]["username"])
            if account["role"] == "student" and not account["disabled"]
            and needle in account["username"].casefold()
        ][:50]

    def search_teachers(self, query: str) -> list[dict[str, Any]]:
        """Usernames only, for adding a co-teacher."""
        self._require_teacher_or_admin()
        needle = str(query or "").strip().casefold()
        if not needle:
            return []
        return [
            {"id": user_id, "username": account["username"]}
            for user_id, account in sorted(_accounts().items(), key=lambda item: item[1]["username"])
            if account["role"] == "teacher" and not account["disabled"]
            and needle in account["username"].casefold()
        ][:50]

    def invite(self, class_id: str, student_id: str) -> dict[str, Any]:
        accounts = _accounts()
        self._require_student(accounts, student_id)
        with store.write() as conn:
            row = self._require_manager(conn, class_id)
            if row["archived"]:
                raise ClassConflict("This class is archived.")
            if conn.execute(
                "SELECT 1 FROM class_members WHERE class_id = ? AND student_id = ?",
                (class_id, student_id),
            ).fetchone():
                raise ClassConflict("This student is already in the class.")
            existing = conn.execute(
                "SELECT id FROM class_invitations WHERE class_id = ? AND student_id = ? AND status = 'pending'",
                (class_id, student_id),
            ).fetchone()
            if existing:
                return {"id": existing["id"], "status": "pending"}
            invitation_id = f"inv_{uuid.uuid4().hex}"
            conn.execute(
                "INSERT INTO class_invitations (id, class_id, student_id, invited_by, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (invitation_id, class_id, student_id, self.actor.id, _now()),
            )
        return {"id": invitation_id, "status": "pending"}

    def cancel_invitation(self, invitation_id: str) -> None:
        with store.write() as conn:
            row = conn.execute(
                "SELECT class_id FROM class_invitations WHERE id = ? AND status = 'pending'",
                (invitation_id,),
            ).fetchone()
            if row is None:
                raise ClassNotFound("Invitation not found.")
            self._require_manager(conn, row["class_id"])
            conn.execute(
                "UPDATE class_invitations SET status = 'cancelled', responded_at = ? WHERE id = ?",
                (_now(), invitation_id),
            )

    def my_invitations(self) -> list[dict[str, Any]]:
        accounts = _accounts()
        with store.read() as conn:
            if conn is None:
                return []
            rows = conn.execute(
                "SELECT i.id, i.class_id, i.invited_by, i.created_at, c.name FROM class_invitations i "
                "JOIN classes c ON c.id = i.class_id "
                "WHERE i.student_id = ? AND i.status = 'pending' AND c.archived = 0 "
                "ORDER BY i.created_at DESC",
                (self.actor.id,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "class_id": row["class_id"],
                "class_name": row["name"],
                "invited_by": self._person(accounts, row["invited_by"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def respond(self, invitation_id: str, *, accept: bool) -> None:
        with store.write() as conn:
            row = conn.execute(
                "SELECT class_id, student_id FROM class_invitations WHERE id = ? AND status = 'pending'",
                (invitation_id,),
            ).fetchone()
            if row is None or row["student_id"] != self.actor.id:
                raise ClassNotFound("Invitation not found.")
            if accept:
                if _role(self.actor) != "student" or self.actor.is_admin:
                    raise ClassForbidden("Only student accounts join classes.")
                if self._class_row(conn, row["class_id"])["archived"]:
                    raise ClassConflict("This class is archived.")
                self._join(conn, row["class_id"], self.actor.id, via="invitation")
            else:
                conn.execute(
                    "UPDATE class_invitations SET status = 'declined', responded_at = ? WHERE id = ?",
                    (_now(), invitation_id),
                )
        if accept:
            _paths_follow(self.actor.id)

    # ── invite links ──────────────────────────────────────────

    def invite_link(self, class_id: str) -> dict[str, Any] | None:
        with store.read() as conn:
            if conn is None:
                raise ClassNotFound("Class not found.")
            self._require_manager(conn, class_id)
            row = conn.execute(
                "SELECT token, created_at, expires_at FROM class_invite_links WHERE class_id = ?",
                (class_id,),
            ).fetchone()
        return dict(row) if row else None

    def regenerate_link(self, class_id: str, *, expires_in_days: int | None = None) -> dict[str, Any]:
        """Issue a new link; the previous one stops working at once (spec §5)."""
        if expires_in_days is not None and not 1 <= int(expires_in_days) <= INVITE_TTL_MAX_DAYS:
            raise ClassError(f"Expiry must be between 1 and {INVITE_TTL_MAX_DAYS} days.")
        now = _now()
        expires_at = now + int(expires_in_days) * 86400 if expires_in_days else None
        token = secrets.token_urlsafe(18)
        with store.write() as conn:
            row = self._require_manager(conn, class_id)
            if row["archived"]:
                raise ClassConflict("This class is archived.")
            conn.execute("DELETE FROM class_invite_links WHERE class_id = ?", (class_id,))
            conn.execute(
                "INSERT INTO class_invite_links VALUES (?, ?, ?, ?, ?)",
                (class_id, token, self.actor.id, now, expires_at),
            )
        return {"token": token, "created_at": now, "expires_at": expires_at}

    def revoke_link(self, class_id: str) -> None:
        with store.write() as conn:
            self._require_manager(conn, class_id)
            conn.execute("DELETE FROM class_invite_links WHERE class_id = ?", (class_id,))

    def join_by_link(self, token: str) -> dict[str, Any]:
        if _role(self.actor) != "student" or self.actor.is_admin:
            raise ClassForbidden("Only student accounts join a class with an invite link.")
        with store.write() as conn:
            row = _live_link(conn, token)
            self._join(conn, row["class_id"], self.actor.id, via="link")
        _paths_follow(self.actor.id)
        return {"class_id": row["class_id"], "class_name": row["name"]}

    # ── teacher reach ─────────────────────────────────────────

    def reset_student_password(self, student_id: str, new_password: str) -> None:
        """Spec D20: administrators, or a teacher for a student in their own class."""
        from deeptutor.multi_user.identity import set_password
        from deeptutor.services.auth import hash_password

        accounts = _accounts()
        self._require_student(accounts, student_id)
        if len(new_password or "") < 8:
            raise ClassError("Password must be at least 8 characters.")
        if not self._is_admin() and (
            _role(self.actor) != "teacher" or student_id not in teacher_student_ids(self.actor.id)
        ):
            raise ClassForbidden("You can only reset passwords for students in your classes.")
        set_password(accounts[student_id]["username"], hash_password(new_password))
        end_other_sessions(student_id, revoked_by=self.actor.id)


def end_other_sessions(user_id: str, *, revoked_by: str) -> None:
    """After a password reset: paired devices stop working too (tokens end via the store)."""
    try:
        from deeptutor.multi_user.device_credentials import revoke_device_credentials_for_user

        revoke_device_credentials_for_user(user_id, revoked_by=revoked_by)
    except Exception:
        logger.exception("Could not revoke the device credentials of %s", user_id)


def _paths_follow(*student_ids: str) -> None:
    """Mastery Paths published to classes follow membership (spec §7.1)."""
    from deeptutor.publishing.scope import reconcile_student_quietly

    for student_id in student_ids:
        reconcile_student_quietly(student_id)


def link_info(token: str) -> dict[str, Any]:
    """What an invite page shows before signing in: the class name only."""
    with store.read() as conn:
        if conn is None:
            raise ClassNotFound("This invite link is not valid.")
        row = _live_link(conn, token)
    return {"class_name": row["name"], "expires_at": row["expires_at"]}


def _live_link(conn, token: str):
    row = conn.execute(
        "SELECT l.class_id, l.expires_at, c.name, c.archived FROM class_invite_links l "
        "JOIN classes c ON c.id = l.class_id WHERE l.token = ?",
        (str(token or ""),),
    ).fetchone()
    if row is None or row["archived"]:
        raise ClassNotFound("This invite link is not valid.")
    if row["expires_at"] is not None and row["expires_at"] < _now():
        raise ClassNotFound("This invite link has expired.")
    return row


def teacher_student_ids(teacher_id: str) -> set[str]:
    """Students in the active classes this teacher teaches."""
    with store.read() as conn:
        if conn is None:
            return set()
        rows = conn.execute(
            "SELECT DISTINCT m.student_id FROM class_members m "
            "JOIN class_teachers t ON t.class_id = m.class_id "
            "JOIN classes c ON c.id = m.class_id "
            "WHERE t.user_id = ? AND c.archived = 0",
            (teacher_id,),
        ).fetchall()
    accounts = _accounts()
    teacher = accounts.get(teacher_id)
    if teacher is None or teacher["role"] != "teacher" or teacher["disabled"]:
        return set()
    # Only accounts that are students now (a promoted student's rows may linger).
    return {
        row["student_id"] for row in rows
        if (accounts.get(row["student_id"]) or {}).get("role") == "student"
        and not accounts[row["student_id"]]["disabled"]
    }


def _class_policy(row) -> dict[str, bool]:
    try:
        value = json.loads(row["student_policy_json"] or "{}")
    except (KeyError, IndexError, ValueError):
        return {}
    return {k: v for k, v in value.items() if isinstance(v, bool)} if isinstance(value, dict) else {}


def class_student_policy(student_id: str) -> dict[str, bool]:
    """What the student's active classes say, per key: any allow wins (spec §6)."""
    with store.read() as conn:
        if conn is None:
            return {}
        rows = conn.execute(
            "SELECT c.student_policy_json FROM classes c JOIN class_members m ON m.class_id = c.id "
            "WHERE m.student_id = ? AND c.archived = 0",
            (student_id,),
        ).fetchall()
    combined: dict[str, bool] = {}
    for row in rows:
        for key, value in _class_policy(row).items():
            combined[key] = combined.get(key, False) or value
    return combined


def student_class_ids(student_id: str) -> set[str]:
    with store.read() as conn:
        if conn is None:
            return set()
        rows = conn.execute(
            "SELECT m.class_id FROM class_members m JOIN classes c ON c.id = m.class_id "
            "WHERE m.student_id = ? AND c.archived = 0",
            (student_id,),
        ).fetchall()
    return {row["class_id"] for row in rows}


__all__ = [
    "ClassConflict",
    "ClassError",
    "ClassForbidden",
    "ClassNotFound",
    "ClassService",
    "class_student_policy",
    "link_info",
    "student_class_ids",
    "teacher_student_ids",
]
