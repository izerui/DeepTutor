"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { useAuthStatus } from "@/hooks/useAuthStatus";
import { listAssignedTasks, type AssignedTask } from "@/lib/tasks-api";

import { formatWhen, STATE_LABEL, STATE_TONE } from "./task-format";

/**
 * The task board's read-only "assigned by teachers" lane (spec 2026-11-03
 * batch 1 §2). It reads the tasks; the student's own cards are untouched.
 */
export function AssignedLane() {
  const { t } = useTranslation();
  const { isAdmin, isTeacher, loading } = useAuthStatus();
  const [tasks, setTasks] = useState<AssignedTask[] | null>(null);
  const student = !loading && !isAdmin && !isTeacher;

  useEffect(() => {
    if (!student) return;
    listAssignedTasks()
      .then((rows) => setTasks(rows.filter((row) => row.state === "open" || row.state === "overdue")))
      .catch(() => setTasks([]));
  }, [student]);

  if (!student || !tasks || tasks.length === 0) return null;
  return (
    <section aria-label={t("From my teachers")} className="mb-4 rounded-2xl border border-border/45 bg-secondary/40 p-3">
      <h2 className="mb-2 px-1 text-[13px] font-semibold">
        {t("From my teachers")} <span className="font-normal text-[var(--muted-foreground)]">· {t("read-only")}</span>
      </h2>
      <ul className="flex gap-2 overflow-x-auto pb-1">
        {tasks.map((task) => (
          <li key={task.key} className="w-56 shrink-0">
            <Link
              href={`/start?task=${encodeURIComponent(task.key)}`}
              className="block rounded-xl border border-[var(--border)] bg-[var(--card)] p-2.5 text-[12.5px] hover:border-[var(--ring)]"
            >
              <span className={`rounded-full px-2 py-0.5 text-[10.5px] font-medium ${STATE_TONE[task.state]}`}>
                {t(STATE_LABEL[task.state])}
              </span>
              <span className="mt-1 block truncate font-medium">{task.title}</span>
              {task.due_at && (
                <span className="text-[11px] text-[var(--muted-foreground)]">{t("Due {{date}}", { date: formatWhen(task.due_at) })}</span>
              )}
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
