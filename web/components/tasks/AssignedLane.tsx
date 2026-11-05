"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { listAssignedTasks, type AssignedTask } from "@/lib/tasks-api";

import { STATE_LABEL, STATE_TONE } from "./task-format";

/**
 * The task board's read-only "from my courses" lane (spec 2026-11-03
 * batch 1 §2). It reads the tasks; the learner's own cards are untouched.
 * Spec 2026-11-05: anyone may join courses, so everyone has the lane.
 */
export function AssignedLane() {
  const { t } = useTranslation();
  const [tasks, setTasks] = useState<AssignedTask[] | null>(null);

  useEffect(() => {
    listAssignedTasks()
      .then((rows) => setTasks(rows.filter((row) => row.state === "open")))
      .catch(() => setTasks([]));
  }, []);

  if (!tasks || tasks.length === 0) return null;
  return (
    <section aria-label={t("From my courses")} className="mb-4 rounded-2xl border border-border/45 bg-secondary/40 p-3">
      <h2 className="mb-2 px-1 text-[13px] font-semibold">
        {t("From my courses")} <span className="font-normal text-[var(--muted-foreground)]">· {t("read-only")}</span>
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
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
