"use client";

import Link from "next/link";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { SourceBadge } from "@/components/common/SourceBadge";
import { markTaskDone, type AssignedTask } from "@/lib/tasks-api";

import { formatWhen, KIND_LABEL, STATE_LABEL, STATE_TONE, targetHref } from "./task-format";

/** One task as the student sees it: what to do and where to do it (spec 2026-11-03 §3–§4). */
export function StudentTaskDetail({ task, onChanged }: { task: AssignedTask; onChanged: (next: AssignedTask) => void }) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const href = targetHref(task);
  // Before it opens a task shows only when it opens (batch 2 §6); nothing can be done yet.
  const closed = task.state === "closed" || task.state === "not_open";

  const run = async (action: () => Promise<AssignedTask>) => {
    setBusy(true);
    setError("");
    try {
      onChanged(await action());
    } catch (err) {
      setError(err instanceof Error ? t(err.message) : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <article className="space-y-3 rounded-xl border border-[var(--border)] p-4" data-testid="student-task-detail">
      <header className="space-y-1.5">
        <div className="flex flex-wrap items-center gap-1.5">
          <SourceBadge provenance={task.provenance} />
          <span className="rounded-full bg-[var(--muted)] px-2 py-0.5 text-[11px]">{t(KIND_LABEL[task.kind])}</span>
          <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${STATE_TONE[task.state]}`}>
            {t(STATE_LABEL[task.state])}
            {task.state === "completed" && task.late ? ` · ${t("Late")}` : ""}
          </span>
        </div>
        <h2 className="text-[16px] font-semibold text-[var(--foreground)]">{task.title}</h2>
        {task.due_at && (
          <p className="text-[12px] text-[var(--muted-foreground)]">{t("Due {{date}}", { date: formatWhen(task.due_at) })}</p>
        )}
      </header>
      {task.description && <p className="whitespace-pre-line text-[13px]">{task.description}</p>}
      {task.kind === "book" && (task.settings.chapter_titles ?? []).length > 0 && (
        <p className="text-[12.5px] text-[var(--muted-foreground)]">
          {t("Chapters: {{names}}", { names: (task.settings.chapter_titles ?? []).join(", ") })}
        </p>
      )}
      {task.kind === "mastery_path" && (task.settings.ratio ?? 1) < 1 && (
        <p className="text-[12.5px] text-[var(--muted-foreground)]">
          {t("Done when {{percent}}% of the points are mastered", { percent: Math.round((task.settings.ratio ?? 1) * 100) })}
        </p>
      )}
      {task.state === "not_open" && (
        <p className="text-[12.5px] text-[var(--muted-foreground)]">
          {t("Opens {{date}}", { date: formatWhen(task.open_at) })}
        </p>
      )}
      {task.state === "closed" && <p className="text-[12.5px] text-[var(--muted-foreground)]">{t("This task is closed. Your own results stay here.")}</p>}
      {href && !closed && (
        <Link href={href} className="inline-flex rounded-lg bg-[var(--primary)] px-3 py-1.5 text-[13px] text-[var(--primary-foreground)]">
          {task.target_title ? t("Open “{{title}}”", { title: task.target_title }) : t("Open")}
        </Link>
      )}
      {task.kind === "note" && !closed && task.state !== "completed" && (
        <button
          type="button"
          disabled={busy}
          onClick={() => void run(() => markTaskDone(task.key))}
          className="rounded-lg bg-[var(--primary)] px-3 py-1.5 text-[13px] text-[var(--primary-foreground)] disabled:opacity-50"
        >
          {t("Mark as done")}
        </button>
      )}
      {error && <p role="alert" className="text-[12px] text-red-600">{error}</p>}
    </article>
  );
}
