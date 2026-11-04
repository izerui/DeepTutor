"""Learning reports of a course or a Mastery Path (platform batch 3 §1).

The class report's numbers, for the people who joined the item instead of a
class's students. Only the item's author, a teacher, reads it: administrators
do not read learning data (D2).
"""

from __future__ import annotations

import csv
import io
from typing import Any

from deeptutor.catalog import enrollment, items
from deeptutor.catalog.listings import ListingForbidden, ListingNotFound
from deeptutor.classes.report import PERIODS, ReportService, _snapshots
from deeptutor.publishing import scope as scopes

KINDS = (scopes.COURSE, scopes.MASTERY_PATH)
#: Learners per query: with an item's tasks or sets, stays under SQLite's 999 parameters.
CHUNK = 400


class LearnerReportService(ReportService):
    def _require_author(self, kind: str, item_id: str) -> items.ItemInfo:
        from deeptutor.classes.service import _role
        from deeptutor.multi_user.ownership import owns

        if kind not in KINDS:
            raise ListingNotFound("Not found.")
        info = items.info(kind, item_id)
        if info is None or not owns(info.owner, self.actor):
            raise ListingNotFound("Not found.")
        if self.actor.is_admin or _role(self.actor) != "teacher":
            raise ListingForbidden("Only the teacher who made it sees its learning report.")
        return info

    def _learners(self, kind: str, item_id: str, owner: str) -> list[str]:
        from deeptutor.classes.service import _accounts

        accounts = _accounts()
        found = enrollment.members(kind, item_id) - {owner, self.actor.id}
        return sorted(
            (u for u in found if u in accounts and not accounts[u]["disabled"]),
            key=lambda u: accounts[u]["username"],
        )

    def _scope_of(self, kind: str, item_id: str) -> tuple[dict[str, str], set[str], list[str]]:
        """(this teacher's paths in it, its practice sets, its task ids)."""
        mine = self._my_template_ids()
        if kind == scopes.MASTERY_PATH:
            return ({item_id: mine[item_id]} if item_id in mine else {}), set(), []
        tasks = items.course_tasks(item_id)
        paths = {t["target_id"]: mine[t["target_id"]] for t in tasks if t["kind"] == "mastery_path" and t["target_id"] in mine}
        sets = {t["target_id"] for t in tasks if t["kind"] == "practice_set" and t["target_id"]}
        return paths, sets, [t["id"] for t in tasks]

    @classmethod
    def _task_progress(cls, task_ids: list[str], learners: list[str]) -> dict[str, dict[str, int]]:
        """In chunks: many learners must not exceed SQLite's parameter limit (review 2)."""
        out: dict[str, dict[str, int]] = {}
        for start in range(0, len(learners), CHUNK):
            out.update(cls._task_progress_chunk(task_ids, learners[start:start + CHUNK]))
        return out if learners else {}

    @staticmethod
    def _task_progress_chunk(task_ids: list[str], learners: list[str]) -> dict[str, dict[str, int]]:
        from deeptutor.tasks.store import db as task_db

        out = {u: {"done": 0, "total": len(task_ids), "to_grade": 0, "returned": 0, "graded": 0, "last": None}
               for u in learners}
        if not task_ids or not learners:
            return out
        task_marks = ",".join("?" for _ in task_ids)
        user_marks = ",".join("?" for _ in learners)
        with task_db.read() as conn:
            if conn is None:
                return out
            for row in conn.execute(
                f"SELECT student_id, COUNT(*), MAX(completed_at) FROM task_completions WHERE task_key IN ({task_marks}) "  # nosec B608
                f"AND student_id IN ({user_marks}) GROUP BY student_id",
                (*task_ids, *learners),
            ):
                out[row[0]]["done"] = int(row[1])
                out[row[0]]["last"] = row[2]
            for row in conn.execute(
                f"SELECT student_id, status, COUNT(*) FROM task_submissions WHERE task_id IN ({task_marks}) "  # nosec B608
                f"AND student_id IN ({user_marks}) GROUP BY student_id, status",
                (*task_ids, *learners),
            ):
                key = {"submitted": "to_grade", "returned": "returned", "graded": "graded"}.get(row[1])
                if key:
                    out[row[0]][key] = int(row[2])
        return out

    @classmethod
    def _period_counts(
        cls, learners: list[str], paths: dict[str, str], sets: set[str], task_ids: list[str], since: float,
    ) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for start in range(0, len(learners), CHUNK):
            out.update(cls._period_counts_chunk(learners[start:start + CHUNK], paths, sets, task_ids, since))
        return out

    @staticmethod
    def _period_counts_chunk(
        learners: list[str], paths: dict[str, str], sets: set[str], task_ids: list[str], since: float,
    ) -> dict[str, dict[str, int]]:
        """Per learner, since *since*: answers on the item's paths and sets, tasks completed — on this item only."""
        from deeptutor.practice_sets import store as practice_store
        from deeptutor.tasks.store import db as task_db

        out = {u: {"answers": 0, "tasks_done": 0} for u in learners}
        if not learners:
            return out
        for user_id in learners:
            for progress, _topic in _snapshots(user_id):
                origin = getattr(progress, "origin", None)
                if origin is None or origin.template_id not in paths:
                    continue
                out[user_id]["answers"] += sum(
                    1 for a in progress.quiz_attempts
                    if not getattr(a, "voided", False) and float(getattr(a, "timestamp", 0) or 0) >= since
                )
        marks = ",".join("?" for _ in learners)
        if sets:
            set_marks = ",".join("?" for _ in sets)
            with practice_store.read() as conn:
                for row in (conn.execute(
                    f"SELECT student_id, COUNT(*) FROM practice_submissions WHERE set_id IN ({set_marks}) "  # nosec B608
                    f"AND student_id IN ({marks}) AND submitted_at >= ? GROUP BY student_id",
                    (*sorted(sets), *learners, since),
                ) if conn else []):
                    out[row[0]]["answers"] += int(row[1])
        if task_ids:
            task_marks = ",".join("?" for _ in task_ids)
            with task_db.read() as conn:
                for row in (conn.execute(
                    f"SELECT student_id, COUNT(*) FROM task_completions WHERE task_key IN ({task_marks}) "  # nosec B608
                    f"AND student_id IN ({marks}) AND completed_at >= ? GROUP BY student_id",
                    (*task_ids, *learners, since),
                ) if conn else []):
                    out[row[0]]["tasks_done"] = int(row[1])
        return out

    def report(self, kind: str, item_id: str, days: int | None = None) -> dict[str, Any]:
        from deeptutor.classes import attention
        from deeptutor.classes.service import _accounts

        info = self._require_author(kind, item_id)
        if days is not None and days not in PERIODS:
            days = None
        learners = self._learners(kind, item_id, info.owner)
        paths, sets, task_ids = self._scope_of(kind, item_id)
        practice = self._my_practice_results(learners)
        progress = self._task_progress(task_ids, learners)
        flags = attention.for_teacher(self.actor.id, learners)
        import time

        since = time.time() - days * 86400 if days else 0.0
        period = self._period_counts(learners, paths, sets, task_ids, since)
        accounts = _accounts()
        rows = []
        outlines = {template_id: _revision_points(template_id) for template_id in paths}
        for user_id in learners:
            report = self._student_report(user_id, paths, detail=False, days=days)
            seen = {p["template_id"] for p in report["teacher_paths"]}
            for template_id, title in paths.items():
                if template_id not in seen:
                    # Not opened yet: the path's points, none mastered.
                    report["teacher_paths"].append({
                        "template_id": template_id, "title": title, "revision": None,
                        "kp_total": len(outlines[template_id]), "kp_mastered": 0,
                        "attempts": {"correct": 0, "partial": 0, "incorrect": 0}, "last_activity": None,
                    })
            mine_sets = [p for p in practice.get(user_id, []) if p["set_id"] in sets]
            tasks = progress.get(user_id) if kind == scopes.COURSE else None
            # Only what happened on this item: a learner's own study elsewhere stays theirs
            # (review: joining must not show the author one's whole learning record).
            stamps = [p["last_activity"] for p in report["teacher_paths"]] + \
                [s["last_submitted_at"] for s in mine_sets] + [(tasks or {}).get("last")]
            rows.append({
                "id": user_id,
                "username": (accounts.get(user_id) or {}).get("username") or user_id,
                "overall": {
                    "last_activity": max((s for s in stamps if s), default=None),
                    # In the chosen period, on this item only.
                    "period_answers": period[user_id]["answers"],
                    "period_tasks_done": period[user_id]["tasks_done"],
                },
                "paths": report["teacher_paths"],
                "practice_sets": mine_sets,
                "tasks": tasks,
                "flags": flags.get(user_id, []),
            })
        return {
            "kind": kind, "id": item_id, "title": info.title, "period_days": days,
            "paths": [{"template_id": t, "title": title} for t, title in paths.items()],
            "practice_sets": sorted(sets),
            "learners": rows,
        }

    def points(self, kind: str, item_id: str, template_id: str) -> dict[str, Any]:
        """One path in the item: who has mastered each knowledge point."""
        from deeptutor.learning.policy import is_mastered

        info = self._require_author(kind, item_id)
        paths, _sets, _tasks = self._scope_of(kind, item_id)
        if template_id not in paths:
            raise ListingNotFound("Mastery Path not found.")
        points: dict[str, dict[str, Any]] = {}
        outline = _revision_points(template_id)
        for user_id in self._learners(kind, item_id, info.owner):
            opened = False
            for progress, _topic in _snapshots(user_id):
                origin = getattr(progress, "origin", None)
                if origin is None or origin.template_id != template_id:
                    continue
                opened = True
                for module in progress.modules:
                    for kp in module.knowledge_points:
                        entry = points.setdefault(kp.id, {"id": kp.id, "name": kp.name, "students": 0,
                                                         "mastered": 0, "not_mastered": []})
                        entry["students"] += 1
                        if is_mastered(progress, kp):
                            entry["mastered"] += 1
                        else:
                            entry["not_mastered"].append(user_id)
            if not opened:
                # Joined but never opened: their copy is made on first opening; nothing mastered yet.
                for kp_id, name in outline:
                    entry = points.setdefault(kp_id, {"id": kp_id, "name": name, "students": 0,
                                                      "mastered": 0, "not_mastered": []})
                    entry["students"] += 1
                    entry["not_mastered"].append(user_id)
        return {"template_id": template_id, "title": paths[template_id], "points": list(points.values())}

    def export(self, kind: str, item_id: str, days: int | None = None) -> str:
        report = self.report(kind, item_id, days)
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow([
            "learner", "last_activity", "answers_in_period", "kp_mastered", "kp_total", "tasks_done", "tasks_total",
            "homework_to_grade", "practice_sets_done", "practice_score_ratio", "attention",
        ])
        for row in report["learners"]:
            overall = row["overall"]
            kp_mastered = sum(p["kp_mastered"] for p in row["paths"])
            kp_total = sum(p["kp_total"] for p in row["paths"])
            sets = row["practice_sets"]
            maximum = sum(s["best_max_score"] or 0 for s in sets)
            ratio = sum(s["best_score"] or 0 for s in sets) / maximum if sets and maximum else None
            tasks = row["tasks"] or {}
            last = overall.get("last_activity")
            writer.writerow([
                row["username"], _when(last), overall.get("period_answers", 0), kp_mastered, kp_total,
                tasks.get("done", ""), tasks.get("total", ""), tasks.get("to_grade", ""), len(sets),
                f"{ratio:.2f}" if ratio is not None else "", "; ".join(flag["kind"] for flag in row["flags"]),
            ])
        return out.getvalue()

    def learner(self, kind: str, item_id: str, user_id: str) -> dict[str, Any]:
        from deeptutor.classes.service import _accounts

        info = self._require_author(kind, item_id)
        if user_id not in self._learners(kind, item_id, info.owner):
            raise ListingNotFound("This person has not joined it.")
        paths, sets, task_ids = self._scope_of(kind, item_id)
        report = self._student_report(user_id, paths, detail=True)
        return {
            "id": user_id,
            "username": (_accounts().get(user_id) or {}).get("username") or user_id,
            "paths": report["teacher_paths"],
            "practice_sets": [p for p in self._my_practice_results([user_id]).get(user_id, []) if p["set_id"] in sets],
            "tasks": self._task_progress(task_ids, [user_id])[user_id] if kind == scopes.COURSE else None,
        }


def _revision_points(template_id: str) -> list[tuple[str, str]]:
    """(id, name) of the knowledge points of the path's current revision."""
    from deeptutor.teaching.store import TeachingStore

    teaching = TeachingStore()
    template = teaching.get_template(template_id)
    revision = teaching.get_revision(template_id, template.current_revision) if template else None
    if revision is None:
        return []
    return [(kp.id, kp.name) for module in revision.outline.modules for kp in module.knowledge_points]


def _when(stamp: Any) -> str:
    import time

    if not stamp:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(stamp)))
    except (TypeError, ValueError):
        return str(stamp)


__all__ = ["KINDS", "LearnerReportService"]
