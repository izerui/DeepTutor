"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { listToGrade, type ToGrade } from "@/lib/workbench-api";

/** Every hand-in waiting for the teacher, oldest first (spec 2026-11-03 batch 3 §5.4). */
export default function GradingQueue() {
  const { t } = useTranslation();
  const [items, setItems] = useState<ToGrade[] | null>(null);
  const [task, setTask] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    listToGrade().then(setItems).catch((err) => setError(err instanceof Error ? t(err.message) : String(err)));
  }, [t]);
  const tasks = new Map((items ?? []).map((item) => [item.task_id, item.task_title]));
  const shown = (items ?? []).filter((item) => !task || item.task_id === task);
  return (
    <div className="space-y-4">
      <header>
        <h1 className="font-serif text-[27px] text-[var(--foreground)]">{t("Grading")}</h1>
        <p className="mt-1 text-[13px] text-[var(--muted-foreground)]">{t("Homework handed in and waiting for your grade.")}</p>
      </header>
      {error && <p role="alert" className="text-[12px] text-red-600">{error}</p>}
      {items === null ? (
        <div aria-busy="true" className="h-24 animate-pulse rounded-xl bg-[var(--muted)]" />
      ) : items.length === 0 ? (
        <p className="text-[12.5px] text-[var(--muted-foreground)]">{t("Nothing to grade.")}</p>
      ) : (
        <>
          <label className="flex items-center gap-2 text-[12.5px]">
            {t("Task")}
            <select className="h-8 rounded-lg border border-[var(--border)] bg-[var(--background)] px-2" value={task}
              onChange={(e) => setTask(e.target.value)}>
              <option value="">{t("All tasks")}</option>
              {[...tasks].map(([id, title]) => <option key={id} value={id}>{title}</option>)}
            </select>
          </label>
          <ul className="divide-y divide-[var(--border)] rounded-xl border border-[var(--border)]" data-testid="grading-queue">
            {shown.map((item) => (
              <li key={`${item.task_id}:${item.student_id}`}>
                <Link href={`/tasks?task=${encodeURIComponent(item.task_id)}`}
                  className="flex flex-wrap items-center gap-2 p-3 text-[13px] hover:bg-[var(--muted)]/40">
                  <span className="font-medium">{item.username}</span>
                  <span className="text-[var(--muted-foreground)]">{item.task_title}</span>
                  {item.attempt > 1 && (
                    <span className="rounded-full bg-[var(--muted)] px-2 text-[11px]">{t("Attempt {{n}}", { n: item.attempt })}</span>
                  )}
                  <span className="ml-auto text-[11.5px] text-[var(--muted-foreground)]">
                    {new Date(item.submitted_at * 1000).toLocaleString()}
                    {item.late && ` · ${t("Late")}`}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
