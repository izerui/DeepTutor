"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { actionClass, inputClass, primaryClass } from "@/components/common/form-styles";
import { useAuthStatus } from "@/hooks/useAuthStatus";
import { bookApi } from "@/lib/book-api";
import type { Book, Chapter } from "@/lib/book-types";
import { listMySets, type PracticeSetSummary } from "@/lib/practice-sets-api";
import {
  archiveTask,
  deleteTask,
  getTask,
  handOverTask,
  publishTask,
  updateTask,
  type TaskDetail,
} from "@/lib/tasks-api";
import { listAuthorTemplates, type AuthorTemplate } from "@/lib/teaching-api";

import { HandOverPanel } from "./HandOver";
import { KIND_LABEL } from "./task-format";

const STATUS_LABEL = { draft: "Draft", published: "Published", archived: "Withdrawn" } as const;

function TargetPicker({
  task,
  onChange,
}: {
  task: TaskDetail;
  onChange: (fields: { target_id?: string; settings?: TaskDetail["settings"] }) => void;
}) {
  const { t } = useTranslation();
  const [sets, setSets] = useState<PracticeSetSummary[]>([]);
  const [paths, setPaths] = useState<AuthorTemplate[]>([]);
  const [books, setBooks] = useState<Book[]>([]);
  const [chapters, setChapters] = useState<Chapter[]>([]);

  useEffect(() => {
    if (task.kind === "practice_set") listMySets().then((rows) => setSets(rows.filter((r) => r.status === "published"))).catch(() => undefined);
    if (task.kind === "mastery_path")
      listAuthorTemplates().then((rows) => setPaths(rows.filter((r) => r.is_owner && r.status === "published"))).catch(() => undefined);
    if (task.kind === "book") bookApi.list().then((r) => setBooks(r.books)).catch(() => undefined);
  }, [task.kind]);

  useEffect(() => {
    if (task.kind !== "book" || !task.target_id) return;
    bookApi.getSpine(task.target_id).then((r) => setChapters(r.spine.chapters)).catch(() => setChapters([]));
  }, [task.kind, task.target_id]);

  if (task.kind === "practice_set" || task.kind === "mastery_path") {
    const options = task.kind === "practice_set"
      ? sets.map((s) => ({ id: s.id, title: s.title }))
      : paths.map((p) => ({ id: p.id, title: p.title }));
    return (
      <div className="space-y-2">
        <select
          aria-label={t("What to do")}
          className={inputClass}
          value={task.target_id}
          onChange={(e) => onChange({ target_id: e.target.value, settings: task.settings })}
        >
          <option value="">{task.kind === "practice_set" ? t("Choose a practice set you published") : t("Choose a Mastery Path you published")}</option>
          {options.map((o) => <option key={o.id} value={o.id}>{o.title}</option>)}
        </select>
        {task.kind === "mastery_path" && (
          <label className="flex items-center gap-2 text-[12.5px]">
            {t("Done when this share of points is mastered")}
            <input
              type="number" min={1} max={100} className={`${inputClass} w-20`}
              value={Math.round((task.settings.ratio ?? 1) * 100)}
              onChange={(e) => onChange({ target_id: task.target_id, settings: { ratio: Math.max(1, Math.min(100, Number(e.target.value) || 100)) / 100 } })}
            />
            %
          </label>
        )}
      </div>
    );
  }
  if (task.kind === "book") {
    const chosen = new Set(task.settings.chapter_ids ?? []);
    return (
      <div className="space-y-2">
        <select
          aria-label={t("Book")}
          className={inputClass}
          value={task.target_id}
          onChange={(e) => onChange({ target_id: e.target.value, settings: { chapter_ids: [] } })}
        >
          <option value="">{t("Choose a book")}</option>
          {books.map((b) => <option key={b.id} value={b.id}>{b.title}</option>)}
        </select>
        {chapters.length > 0 && (
          <fieldset className="space-y-1 text-[12.5px]">
            <legend className="font-medium">{t("Chapters to read")}</legend>
            {chapters.map((c) => (
              <label key={c.id} className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={chosen.has(c.id)}
                  onChange={() => {
                    const next = chosen.has(c.id) ? [...chosen].filter((id) => id !== c.id) : [...chosen, c.id];
                    onChange({ target_id: task.target_id, settings: { chapter_ids: next } });
                  }}
                />
                {c.title}
              </label>
            ))}
          </fieldset>
        )}
      </div>
    );
  }
  return null;
}

/**
 * One lesson of one's own course, opened from the course (spec 2026-11-06 §1:
 * lessons are seen inside their course; the API still calls them tasks).
 */
export function LessonEditor({
  taskId,
  onChanged,
  onDeleted,
  onLoaded,
  onMissing,
}: {
  taskId: string;
  onChanged: () => void;
  onDeleted: () => void;
  /** Told once the lesson is read, so the course holding it can be shown beside it. */
  onLoaded?: (task: TaskDetail) => void;
  /** The lesson is not one's own (or is gone): ``?task=`` may name a lesson one joined. */
  onMissing?: () => void;
}) {
  const { t } = useTranslation();
  const { isAdmin } = useAuthStatus();
  const [task, setTask] = useState<TaskDetail | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const told = useRef({ onLoaded, onMissing, first: true });
  told.current.onLoaded = onLoaded;
  told.current.onMissing = onMissing;

  const load = useCallback(() => {
    getTask(taskId)
      .then((next) => {
        setTask(next);
        if (told.current.first) told.current.onLoaded?.(next);
        told.current.first = false;
      })
      .catch((err) => {
        if (told.current.first && told.current.onMissing) return told.current.onMissing();
        setError(err instanceof Error ? t(err.message) : String(err));
      });
  }, [taskId, t]);
  useEffect(load, [load]);

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await action();
      load();
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? t(err.message) : String(err));
    } finally {
      setBusy(false);
    }
  };

  if (!task) return error ? <p role="alert" className="text-[12px] text-red-600">{error}</p> : <div aria-busy="true" className="h-40 animate-pulse rounded-xl bg-[var(--muted)]" />;
  const draft = task.status === "draft";
  const save = (fields: Parameters<typeof updateTask>[1]) => {
    setTask({ ...task, ...fields } as TaskDetail);
    void updateTask(task.id, fields).catch((err) => setError(err instanceof Error ? t(err.message) : String(err)));
  };

  return (
    <fieldset disabled={busy} className="min-w-0 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <input
          className={`${inputClass} max-w-md text-[15px] font-semibold`}
          aria-label={t("Title")}
          value={task.title}
          maxLength={200}
          onChange={(e) => setTask({ ...task, title: e.target.value })}
          onBlur={() => save({ title: task.title })}
        />
        <span className="text-[12px] text-[var(--muted-foreground)]">{t(KIND_LABEL[task.kind])} · {t(STATUS_LABEL[task.status])}</span>
      </div>
      <textarea
        aria-label={t("Instructions for learners")}
        placeholder={t("Instructions for learners")}
        rows={4}
        className={inputClass}
        value={task.description}
        onChange={(e) => setTask({ ...task, description: e.target.value })}
        onBlur={() => save({ description: task.description })}
      />
      {draft ? (
        <TargetPicker task={task} onChange={(fields) => save(fields)} />
      ) : task.target_title ? (
        <p className="text-[12.5px]">{t("Asks for: {{title}}", { title: task.target_title })}</p>
      ) : null}

      {draft && !task.course_id && (
        <p className="text-[11.5px] text-[var(--muted-foreground)]">
          {t("This lesson is in no course. Add it to a course; it is published with the course.")}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        {/* Platform batch 2 §5: a task goes out with its course, never on its own. */}
        {draft && task.course_id && (
          <button type="button" className={primaryClass} onClick={() => void run(() => publishTask(task.id))}>
            {t("Publish")}
          </button>
        )}
        {task.status === "published" && (
          <button type="button" className={actionClass}
            onClick={() => window.confirm(t("Withdraw this lesson? Learners will see it as closed.")) && void run(() => archiveTask(task.id))}>
            {t("Withdraw")}
          </button>
        )}
        {draft && (
          <button type="button" className={actionClass}
            onClick={() => void deleteTask(task.id).then(onDeleted).catch((err) => setError(err instanceof Error ? t(err.message) : String(err)))}>
            {t("Delete")}
          </button>
        )}
      </div>
      {/* Traceability #5: a task in no course goes on its own to a learner. */}
      {isAdmin && !task.course_id && (
        <HandOverPanel handOver={(learnerId) => handOverTask(task.id, learnerId)} onDone={onDeleted} />
      )}
      {error && <p role="alert" className="text-[12px] text-red-600">{error}</p>}

      {!draft && <LearnersTable task={task} />}
    </fieldset>
  );
}

function LearnersTable({ task }: { task: TaskDetail }) {
  const { t } = useTranslation();
  return (
    <section className="space-y-2">
      <h3 className="text-[13px] font-semibold">{t("Learners")}</h3>
      {task.students.length === 0 ? (
        <p className="text-[12.5px] text-[var(--muted-foreground)]">{t("No learners receive this yet.")}</p>
      ) : (
        <ul className="divide-y divide-[var(--border)] rounded-xl border border-[var(--border)]">
          {task.students.map((learner) => (
            <li key={learner.id} className="flex flex-wrap items-center gap-2 p-3 text-[13px]">
              <span className="font-medium">{learner.username}</span>
              {!learner.in_scope && <span className="text-[11.5px] text-[var(--muted-foreground)]">{t("Not in scope")}</span>}
              <span className="text-[11.5px] text-[var(--muted-foreground)]">
                {learner.completed
                  ? learner.self_reported ? t("Done (self-reported)") : t("Done")
                  : t("Not done")}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
