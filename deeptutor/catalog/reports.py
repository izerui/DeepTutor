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

    @staticmethod
    def _task_progress(task_ids: list[str], learners: list[str]) -> dict[str, dict[str, int]]:
        from deeptutor.tasks.store import db as task_db

        out = {u: {"done": 0, "total": len(task_ids), "to_grade": 0, "returned": 0, "graded": 0} for u in learners}
        if not task_ids or not learners:
            return out
        task_marks = ",".join("?" for _ in task_ids)
        user_marks = ",".join("?" for _ in learners)
        with task_db.read() as conn:
            if conn is None:
                return out
            for row in conn.execute(
                f"SELECT student_id, COUNT(*) FROM task_completions WHERE task_key IN ({task_marks}) "  # nosec B608
                f"AND student_id IN ({user_marks}) GROUP BY student_id",
                (*task_ids, *learners),
            ):
                out[row[0]]["done"] = int(row[1])
            for row in conn.execute(
                f"SELECT student_id, status, COUNT(*) FROM task_submissions WHERE task_id IN ({task_marks}) "  # nosec B608
                f"AND student_id IN ({user_marks}) GROUP BY student_id, status",
                (*task_ids, *learners),
            ):
                key = {"submitted": "to_grade", "returned": "returned", "graded": "graded"}.get(row[1])
                if key:
                    out[row[0]][key] = int(row[2])
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
        accounts = _accounts()
        rows = []
        for user_id in learners:
            report = self._student_report(user_id, paths, detail=False, days=days)
            rows.append({
                "id": user_id,
                "username": (accounts.get(user_id) or {}).get("username") or user_id,
                "overall": report["overall"],
                "paths": report["teacher_paths"],
                "practice_sets": [p for p in practice.get(user_id, []) if p["set_id"] in sets],
                "tasks": progress.get(user_id) if kind == scopes.COURSE else None,
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
        for user_id in self._learners(kind, item_id, info.owner):
            for progress, _topic in _snapshots(user_id):
                origin = getattr(progress, "origin", None)
                if origin is None or origin.template_id != template_id:
                    continue
                for module in progress.modules:
                    for kp in module.knowledge_points:
                        entry = points.setdefault(kp.id, {"id": kp.id, "name": kp.name, "students": 0,
                                                         "mastered": 0, "not_mastered": []})
                        entry["students"] += 1
                        if is_mastered(progress, kp):
                            entry["mastered"] += 1
                        else:
                            entry["not_mastered"].append(user_id)
        return {"template_id": template_id, "title": paths[template_id], "points": list(points.values())}

    def export(self, kind: str, item_id: str, days: int | None = None) -> str:
        report = self.report(kind, item_id, days)
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow([
            "learner", "study_minutes", "answers", "kp_mastered", "kp_total", "tasks_done", "tasks_total",
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
            writer.writerow([
                row["username"], overall["period_minutes"], overall["period_answers"], kp_mastered, kp_total,
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


__all__ = ["KINDS", "LearnerReportService"]
