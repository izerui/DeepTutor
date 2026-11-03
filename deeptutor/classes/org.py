"""Departments and grades (spec 2026-11-03 batch 2 §0).

Two attributes of a class, both optional; no tree. Administrators keep the
lists; whoever manages a class picks from them. Administrators publish to a
whole department, a whole grade, or a department's grade — encoded as publish
"group" targets ``department:<id>``, ``grade:<id>`` and
``department:<id>+grade:<id>`` — and later classes that match are included.
A teacher may be listed under departments, only so administrators can find
them by department; it grants nothing.

Older class databases have no such tables until their next write: every read
here treats a missing table as "nothing set".
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any
import uuid

from deeptutor.classes import store
from deeptutor.multi_user.context import get_current_user
from deeptutor.multi_user.models import CurrentUser

NAME_MAX = 60


class OrgError(Exception):
    status = 400


class OrgForbidden(OrgError):
    status = 403


class OrgNotFound(OrgError):
    status = 404


def _rows(conn, sql: str, params: tuple = ()) -> list:
    if conn is None:
        return []
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []  # an older database without the table: nothing is set


def _require_admin(actor: CurrentUser) -> None:
    if not actor.is_admin:
        raise OrgForbidden("Only administrators manage departments and grades.")


def _clean(name: str) -> str:
    name = " ".join(str(name or "").split())
    if not name or len(name) > NAME_MAX:
        raise OrgError(f"A name must contain 1 to {NAME_MAX} characters.")
    return name


# ── the lists ──────────────────────────────────────────────


def lists() -> dict[str, list[dict[str, Any]]]:
    with store.read() as conn:
        departments = _rows(conn, "SELECT id, name FROM departments ORDER BY name")
        grades = _rows(conn, "SELECT id, name FROM grades ORDER BY position, name")
    return {
        "departments": [{"id": r["id"], "name": r["name"]} for r in departments],
        "grades": [{"id": r["id"], "name": r["name"]} for r in grades],
    }


def _table(kind: str) -> str:
    if kind not in ("department", "grade"):
        raise OrgNotFound("Unknown kind.")
    return "departments" if kind == "department" else "grades"


def create(kind: str, name: str, actor: CurrentUser | None = None) -> dict[str, Any]:
    _require_admin(actor or get_current_user())
    table = _table(kind)
    item_id = f"{'dep' if kind == 'department' else 'grd'}_{uuid.uuid4().hex[:10]}"
    with store.write() as conn:
        try:
            if table == "grades":
                position = conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM grades").fetchone()[0]
                conn.execute("INSERT INTO grades VALUES (?, ?, ?, ?)", (item_id, _clean(name), position, time.time()))
            else:
                conn.execute("INSERT INTO departments VALUES (?, ?, ?)", (item_id, _clean(name), time.time()))
        except sqlite3.IntegrityError as exc:
            raise OrgError("That name is already used.") from exc
    return {"id": item_id, "name": _clean(name)}


def rename(kind: str, item_id: str, name: str, actor: CurrentUser | None = None) -> None:
    _require_admin(actor or get_current_user())
    table = _table(kind)
    with store.write() as conn:
        try:
            changed = conn.execute(
                f"UPDATE {table} SET name = ? WHERE id = ?", (_clean(name), item_id)  # nosec B608 - fixed tables
            ).rowcount
        except sqlite3.IntegrityError as exc:
            raise OrgError("That name is already used.") from exc
        if not changed:
            raise OrgNotFound("Not found.")


def delete(kind: str, item_id: str, actor: CurrentUser | None = None) -> None:
    """Classes that used it are left without one (§0); publish targets naming it drop it."""
    _require_admin(actor or get_current_user())
    table = _table(kind)
    column = "department_id" if kind == "department" else "grade_id"
    with store.write() as conn:
        if not conn.execute(f"DELETE FROM {table} WHERE id = ?", (item_id,)).rowcount:  # nosec B608
            raise OrgNotFound("Not found.")
        conn.execute(f"UPDATE class_org SET {column} = '' WHERE {column} = ?", (item_id,))  # nosec B608
    from deeptutor.publishing import store as scope_store

    if scope_store.scopes_db_path().exists():
        with scope_store.write() as conn:
            conn.execute(
                "DELETE FROM publish_targets WHERE target_type = 'group' AND "
                "(target_id = ? OR target_id LIKE ? OR target_id LIKE ?)",
                (f"{kind}:{item_id}", f"{kind}:{item_id}+%", f"%+{kind}:{item_id}"),
            )
    from deeptutor.publishing import scope as scopes

    # Paths that went to it reach nobody through it any more.
    for template_id in scopes.managed_items(scopes.MASTERY_PATH):
        try:
            scopes.reconcile_template(template_id)
        except Exception:
            import logging

            logging.getLogger(__name__).exception("Could not re-sync teacher path %s", template_id)


# ── classes ────────────────────────────────────────────────


def class_org_map() -> dict[str, dict[str, str]]:
    """Class id -> its department and grade ids (only classes that have one)."""
    with store.read() as conn:
        rows = _rows(conn, "SELECT class_id, department_id, grade_id FROM class_org")
    return {r["class_id"]: {"department_id": r["department_id"], "grade_id": r["grade_id"]} for r in rows}


def set_class_org(class_id: str, department_id: str, grade_id: str) -> None:
    """Called by the class service once it checked the actor manages the class."""
    department_id, grade_id = str(department_id or ""), str(grade_id or "")
    with store.write() as conn:
        if department_id and not conn.execute("SELECT 1 FROM departments WHERE id = ?", (department_id,)).fetchone():
            raise OrgError("Choose a department from the list.")
        if grade_id and not conn.execute("SELECT 1 FROM grades WHERE id = ?", (grade_id,)).fetchone():
            raise OrgError("Choose a grade from the list.")
        conn.execute(
            "INSERT INTO class_org VALUES (?, ?, ?) ON CONFLICT(class_id) DO UPDATE SET "
            "department_id = excluded.department_id, grade_id = excluded.grade_id",
            (class_id, department_id, grade_id),
        )


# ── publish groups ─────────────────────────────────────────


def parse_group(group: str) -> dict[str, str]:
    """``department:<id>``, ``grade:<id>`` or both joined by ``+``."""
    parts: dict[str, str] = {}
    for piece in str(group or "").split("+"):
        kind, _, item_id = piece.partition(":")
        if kind not in ("department", "grade") or not item_id or kind in parts:
            raise OrgError("Unknown group.")
        parts[kind] = item_id
    return parts


def valid_group(group: str) -> bool:
    try:
        parts = parse_group(group)
    except OrgError:
        return False
    names = lists()
    known = {
        "department": {d["id"] for d in names["departments"]},
        "grade": {g["id"] for g in names["grades"]},
    }
    return all(item_id in known[kind] for kind, item_id in parts.items())


def group_class_ids(groups: list[str]) -> set[str]:
    """Active classes a set of groups covers, later classes included."""
    if not groups:
        return set()
    wanted = []
    for group in groups:
        try:
            wanted.append(parse_group(group))
        except OrgError:
            continue
    with store.read() as conn:
        rows = _rows(
            conn,
            "SELECT o.class_id, o.department_id, o.grade_id FROM class_org o "
            "JOIN classes c ON c.id = o.class_id WHERE c.archived = 0",
        )
    return {
        r["class_id"] for r in rows
        if any(
            all((r["department_id"] if kind == "department" else r["grade_id"]) == item_id
                for kind, item_id in parts.items())
            for parts in wanted
        )
    }


def group_label(group: str, names: dict[str, list[dict[str, Any]]] | None = None) -> str:
    names = names or lists()
    by_id = {d["id"]: d["name"] for d in names["departments"]} | {g["id"]: g["name"] for g in names["grades"]}
    try:
        parts = parse_group(group)
    except OrgError:
        return group
    return " · ".join(by_id.get(parts[k], "?") for k in ("department", "grade") if k in parts)


# ── teachers and departments ───────────────────────────────


def teacher_departments() -> dict[str, list[str]]:
    with store.read() as conn:
        rows = _rows(conn, "SELECT user_id, department_id FROM teacher_departments")
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r["user_id"], []).append(r["department_id"])
    return out


def set_teacher_departments(user_id: str, department_ids: list[str], actor: CurrentUser | None = None) -> None:
    """Only for finding teachers by department (§0); it grants nothing."""
    _require_admin(actor or get_current_user())
    wanted = list(dict.fromkeys(str(d) for d in department_ids if d))
    with store.write() as conn:
        known = {r[0] for r in conn.execute("SELECT id FROM departments")}
        if any(d not in known for d in wanted):
            raise OrgError("Choose departments from the list.")
        conn.execute("DELETE FROM teacher_departments WHERE user_id = ?", (user_id,))
        conn.executemany("INSERT INTO teacher_departments VALUES (?, ?)", [(user_id, d) for d in wanted])


def forget_teacher(user_id: str) -> None:
    if not store.classes_db_path().exists():
        return
    with store.write() as conn:
        conn.execute("DELETE FROM teacher_departments WHERE user_id = ?", (user_id,))


__all__ = [
    "OrgError",
    "OrgForbidden",
    "OrgNotFound",
    "class_org_map",
    "create",
    "delete",
    "group_class_ids",
    "group_label",
    "lists",
    "parse_group",
    "rename",
    "set_class_org",
    "set_teacher_departments",
    "teacher_departments",
    "valid_group",
]
