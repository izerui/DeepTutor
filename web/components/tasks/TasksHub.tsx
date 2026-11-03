"use client";

import { Plus } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { actionClass, inputClass, primaryClass } from "@/components/classes/ClassesHub";
import { FirstPublishScope, type FirstPublishChoice } from "@/components/publishing/FirstPublishScope";
import { PublishScopeEditor } from "@/components/publishing/PublishScopeEditor";
import { useAuthStatus } from "@/hooks/useAuthStatus";
import { bookApi } from "@/lib/book-api";
import type { Book, Chapter } from "@/lib/book-types";
import { listMySets, type PracticeSetSummary } from "@/lib/practice-sets-api";
import {
  archiveTask,
  createTask,
  deleteTask,
  getTask,
  gradeHomework,
  homeworkFileUrl,
  listAssignedTasks,
  listMyTasks,
  publishTask,
  sendBackHomework,
  updateTask,
  type AssignedTask,
  type MyTask,
  type TaskDetail,
  type TaskKind,
} from "@/lib/tasks-api";
import { listTeacherTemplates, type TeacherTemplate } from "@/lib/teaching-api";

import { CourseAuthorView, StudentCourses } from "./Courses";
import { StudentTaskDetail } from "./StudentTaskDetail";
import { formatWhen, KIND_LABEL, STATE_LABEL, STATE_TONE } from "./task-format";

const STATUS_LABEL = { draft: "Draft", published: "Published", archived: "Withdrawn" } as const;
const toLocalInput = (ts: number | null) =>
  ts ? new Date(ts * 1000 - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16) : "";
const fromLocalInput = (value: string) => (value ? new Date(value).getTime() / 1000 : null);

/**
 * Tasks (spec 2026-11-03 batch 1): teachers and administrators assign them and
 * grade homework; students see every task they received, by state.
 */
export default function TasksHub() {
  const { t } = useTranslation();
  const { isAdmin, isTeacher, loading } = useAuthStatus();
  if (loading) return <div aria-busy="true" className="h-40 animate-pulse rounded-xl bg-[var(--muted)]" />;
  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-serif text-[27px] text-[var(--foreground)]">{t("Tasks")}</h1>
        <p className="mt-1 text-[13px] text-[var(--muted-foreground)]">
          {isAdmin || isTeacher
            ? t("Assign work to your classes, collect homework and grade it.")
            : t("Everything your teachers assigned to you.")}
        </p>
      </header>
      {isAdmin || isTeacher ? <AuthorTabs isAdmin={isAdmin} /> : <StudentView />}
    </div>
  );
}

/** Tasks one by one, or courses that lay them out in order (batch 2 §5). */
function AuthorTabs({ isAdmin }: { isAdmin: boolean }) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<"tasks" | "courses">("tasks");
  const [openTask, setOpenTask] = useState<string | null>(null);
  const tabClass = (on: boolean) =>
    `rounded-lg px-3 py-1.5 text-[13px] ${on ? "bg-[var(--muted)] font-medium text-[var(--foreground)]" : "text-[var(--muted-foreground)] hover:bg-[var(--muted)]"}`;
  return (
    <div className="space-y-4">
      <div role="tablist" className="flex gap-1">
        <button type="button" role="tab" aria-selected={tab === "tasks"} className={tabClass(tab === "tasks")} onClick={() => setTab("tasks")}>
          {t("Tasks")}
        </button>
        <button type="button" role="tab" aria-selected={tab === "courses"} className={tabClass(tab === "courses")} onClick={() => setTab("courses")}>
          {t("Courses")}
        </button>
      </div>
      {tab === "tasks" ? (
        <AuthorView key={openTask ?? ""} isAdmin={isAdmin} initialTask={openTask} />
      ) : (
        <CourseAuthorView isAdmin={isAdmin} onOpenTask={(id) => { setOpenTask(id); setTab("tasks"); }} />
      )}
    </div>
  );
}

function AuthorView({ isAdmin, initialTask = null }: { isAdmin: boolean; initialTask?: string | null }) {
  const { t } = useTranslation();
  const [tasks, setTasks] = useState<MyTask[] | null>(null);
  // ``?task=`` (a notification's link) opens one task, also when already on this page.
  const taskParam = useSearchParams().get("task");
  const [selected, setSelected] = useState<string | null>(initialTask ?? taskParam);
  const [seenParam, setSeenParam] = useState(taskParam);
  if (taskParam !== seenParam) {
    setSeenParam(taskParam);
    if (taskParam) setSelected(taskParam);
  }
  const [kind, setKind] = useState<TaskKind>("homework");
  const [title, setTitle] = useState("");
  const [error, setError] = useState("");

  const refresh = useCallback(() => {
    listMyTasks()
      .then(setTasks)
      .catch((err) => setError(err instanceof Error ? t(err.message) : String(err)));
  }, [t]);
  useEffect(refresh, [refresh]);

  const kinds: TaskKind[] = isAdmin
    ? ["practice_set", "mastery_path", "book", "note"] // R21: no homework from administrators
    : ["homework", "practice_set", "mastery_path", "book", "note"];

  return (
    <div className="grid gap-5 lg:grid-cols-[300px_1fr]">
      <aside className="space-y-3">
        <form
          className="space-y-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (!title.trim()) return;
            createTask(isAdmin && kind === "homework" ? "note" : kind, title.trim())
              .then((created) => {
                setTitle("");
                setSelected(created.id);
                refresh();
              })
              .catch((err) => setError(err instanceof Error ? t(err.message) : String(err)));
          }}
        >
          <select
            aria-label={t("Kind of task")}
            className={inputClass}
            value={isAdmin && kind === "homework" ? "practice_set" : kind}
            onChange={(e) => setKind(e.target.value as TaskKind)}
          >
            {kinds.map((k) => (
              <option key={k} value={k}>{t(KIND_LABEL[k])}</option>
            ))}
          </select>
          <div className="flex gap-2">
            <input
              className={inputClass}
              value={title}
              maxLength={200}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={t("New task title")}
            />
            <button type="submit" className={primaryClass} disabled={!title.trim()} aria-label={t("Create")}>
              <Plus size={14} />
            </button>
          </div>
        </form>
        {error && <p role="alert" className="text-[12px] text-[var(--destructive)]">{error}</p>}
        {tasks === null ? (
          <div aria-busy="true" className="h-24 animate-pulse rounded-xl bg-[var(--muted)]" />
        ) : tasks.length === 0 ? (
          <p className="text-[12.5px] text-[var(--muted-foreground)]">{t("No tasks yet.")}</p>
        ) : (
          <ul className="space-y-1">
            {tasks.map((task) => (
              <li key={task.id}>
                <button
                  type="button"
                  onClick={() => setSelected(task.id)}
                  className={`w-full rounded-lg px-3 py-2 text-left text-[13px] hover:bg-[var(--muted)] ${
                    selected === task.id ? "bg-[var(--muted)]" : ""
                  }`}
                >
                  <span className="block truncate font-medium text-[var(--foreground)]">{task.title}</span>
                  <span className="text-[11.5px] text-[var(--muted-foreground)]">
                    {t(KIND_LABEL[task.kind])} · {t(STATUS_LABEL[task.status])}
                    {task.status !== "draft" && ` · ${t("{{done}} / {{total}} done", { done: task.completed, total: task.total })}`}
                    {task.to_grade > 0 && ` · ${t("{{count}} to grade", { count: task.to_grade })}`}
                    {!task.target_available && ` · ${t("Content unavailable")}`}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </aside>
      <section className="min-w-0">
        {selected ? (
          <TaskEditor key={selected} taskId={selected} onChanged={refresh} onDeleted={() => { setSelected(null); refresh(); }} />
        ) : (
          <div className="flex h-40 items-center justify-center rounded-xl border border-dashed border-[var(--border)] text-[13px] text-[var(--muted-foreground)]">
            {t("Pick a task or create one.")}
          </div>
        )}
      </section>
    </div>
  );
}

function TargetPicker({
  task,
  onChange,
}: {
  task: TaskDetail;
  onChange: (fields: { target_id?: string; settings?: TaskDetail["settings"] }) => void;
}) {
  const { t } = useTranslation();
  const [sets, setSets] = useState<PracticeSetSummary[]>([]);
  const [paths, setPaths] = useState<TeacherTemplate[]>([]);
  const [books, setBooks] = useState<Book[]>([]);
  const [chapters, setChapters] = useState<Chapter[]>([]);

  useEffect(() => {
    if (task.kind === "practice_set") listMySets().then((rows) => setSets(rows.filter((r) => r.status === "published"))).catch(() => undefined);
    if (task.kind === "mastery_path")
      listTeacherTemplates().then((rows) => setPaths(rows.filter((r) => r.is_owner && r.status === "published"))).catch(() => undefined);
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
  if (task.kind === "homework") {
    return (
      <div className="space-y-2 text-[12.5px]">
        <label className="flex items-center gap-2">
          {t("Full mark")}
          <input
            type="number" min={1} max={1000} className={`${inputClass} w-24`}
            value={task.settings.max_score ?? 100}
            onChange={(e) => onChange({ settings: { ...task.settings, max_score: Number(e.target.value) || 100 } })}
          />
        </label>
        <label className="block">
          {t("Grading points (optional, the AI suggestion follows them)")}
          <textarea
            rows={3} className={`${inputClass} mt-1`}
            value={task.settings.rubric ?? ""}
            onChange={(e) => onChange({ settings: { ...task.settings, rubric: e.target.value } })}
          />
        </label>
      </div>
    );
  }
  return null;
}

function TaskEditor({ taskId, onChanged, onDeleted }: { taskId: string; onChanged: () => void; onDeleted: () => void }) {
  const { t } = useTranslation();
  const [task, setTask] = useState<TaskDetail | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [first, setFirst] = useState<FirstPublishChoice>({ needed: true, ready: false });

  const load = useCallback(() => {
    getTask(taskId).then(setTask).catch((err) => setError(err instanceof Error ? t(err.message) : String(err)));
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
        aria-label={t("Instructions for students")}
        placeholder={t("Instructions for students")}
        rows={4}
        className={inputClass}
        value={task.description}
        onChange={(e) => setTask({ ...task, description: e.target.value })}
        onBlur={() => save({ description: task.description })}
      />
      <label className="flex flex-wrap items-center gap-2 text-[12.5px]">
        {t("Deadline (optional)")}
        <input
          type="datetime-local"
          className={`${inputClass} w-auto`}
          value={toLocalInput(task.due_at)}
          onChange={(e) => save({ due_at: fromLocalInput(e.target.value) })}
        />
      </label>
      <label className="flex flex-wrap items-center gap-2 text-[12.5px]">
        {t("Opens at (optional)")}
        <input
          type="datetime-local"
          className={`${inputClass} w-auto`}
          value={toLocalInput(task.open_at)}
          onChange={(e) => save({ open_at: fromLocalInput(e.target.value) })}
        />
      </label>
      {task.open_at && (task.kind === "practice_set" || task.kind === "mastery_path") && (
        <p className="text-[11.5px] text-[var(--muted-foreground)]">
          {t("Students who already received this practice set or path on its own can still open it before this time.")}
        </p>
      )}
      {draft ? (
        <TargetPicker task={task} onChange={(fields) => save(fields)} />
      ) : task.target_title ? (
        <p className="text-[12.5px]">{t("Asks for: {{title}}", { title: task.target_title })}</p>
      ) : null}

      {draft && <FirstPublishScope kind="task" itemId={task.id} onChange={setFirst} />}
      <div className="flex flex-wrap gap-2">
        {draft && (
          <button type="button" className={primaryClass} disabled={!first.ready}
            onClick={() => void run(() => publishTask(task.id, first.scope))}>
            {t("Publish")}
          </button>
        )}
        {task.status === "published" && (
          <button type="button" className={actionClass}
            onClick={() => window.confirm(t("Withdraw this task? Students will see it as closed.")) && void run(() => archiveTask(task.id))}>
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
      {error && <p role="alert" className="text-[12px] text-red-600">{error}</p>}

      {task.status === "published" && (
        <div className="rounded-xl border border-[var(--border)] p-3">
          <PublishScopeEditor kind="task" itemId={task.id} onSaved={load} />
        </div>
      )}
      {!draft && <StudentsTable task={task} onChanged={(next) => { setTask(next); onChanged(); }} />}
    </fieldset>
  );
}

function StudentsTable({ task, onChanged }: { task: TaskDetail; onChanged: (next: TaskDetail) => void }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState<string | null>(null);
  return (
    <section className="space-y-2">
      <h3 className="text-[13px] font-semibold">{t("Students")}</h3>
      {task.students.length === 0 ? (
        <p className="text-[12.5px] text-[var(--muted-foreground)]">{t("No students receive this yet.")}</p>
      ) : (
        <ul className="divide-y divide-[var(--border)] rounded-xl border border-[var(--border)]">
          {task.students.map((student) => (
            <li key={student.id} className="p-3 text-[13px]">
              <button type="button" className="flex w-full flex-wrap items-center gap-2 text-left" onClick={() => setOpen(open === student.id ? null : student.id)}>
                <span className="font-medium">{student.username}</span>
                {!student.in_scope && <span className="text-[11.5px] text-[var(--muted-foreground)]">{t("Not in scope")}</span>}
                <span className="text-[11.5px] text-[var(--muted-foreground)]">
                  {task.kind === "homework"
                    ? student.submission
                      ? student.submission.status === "graded"
                        ? t("Graded: {{score}} / {{max}}", { score: student.submission.score, max: task.settings.max_score ?? 100 })
                        : student.submission.status === "returned" ? t("Sent back") : t("To grade")
                      : t("Not handed in")
                    : student.completed
                      ? student.self_reported ? t("Done (self-reported)") : t("Done")
                      : t("Not done")}
                  {(student.late || student.submission?.late) && ` · ${t("Late")}`}
                </span>
              </button>
              {open === student.id && task.kind === "homework" && student.submission && (
                <GradePanel task={task} studentId={student.id} onChanged={onChanged} />
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function GradePanel({ task, studentId, onChanged }: { task: TaskDetail; studentId: string; onChanged: (next: TaskDetail) => void }) {
  const { t } = useTranslation();
  const submission = task.students.find((s) => s.id === studentId)!.submission!;
  const [score, setScore] = useState<string>(String(submission.score ?? submission.ai_score ?? ""));
  const [comment, setComment] = useState(submission.comment ?? "");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const run = async (action: () => Promise<TaskDetail>) => {
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
    <div className="mt-2 space-y-2 rounded-lg bg-[var(--muted)]/40 p-3" data-testid="grade-panel">
      <p className="text-[11.5px] text-[var(--muted-foreground)]">
        {t("Handed in {{date}} (attempt {{n}})", { date: formatWhen(submission.submitted_at), n: submission.attempt })}
      </p>
      <p className="whitespace-pre-line">{submission.text}</p>
      {submission.files.length > 0 && (
        <ul className="text-[12.5px]">
          {submission.files.map((f) => (
            <li key={f.name}>
              <a className="text-[var(--primary)] hover:underline" href={homeworkFileUrl(task.id, studentId, f.name)} target="_blank" rel="noreferrer">
                {f.name}
              </a>
            </li>
          ))}
        </ul>
      )}
      {submission.ai_score != null && (
        <p className="text-[12.5px]">
          {t("AI suggests {{score}}: {{reason}}", { score: submission.ai_score, reason: submission.ai_reason })}{" "}
          <button type="button" className="text-[var(--primary)] hover:underline" onClick={() => setScore(String(submission.ai_score))}>
            {t("Use it")}
          </button>
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <input type="number" aria-label={t("Score")} className={`${inputClass} w-24`} value={score} min={0} max={task.settings.max_score ?? 100}
          onChange={(e) => setScore(e.target.value)} />
        <span className="text-[12px]">/ {task.settings.max_score ?? 100}</span>
      </div>
      <textarea aria-label={t("Comment")} placeholder={t("Comment")} rows={3} className={inputClass} value={comment} onChange={(e) => setComment(e.target.value)} />
      <div className="flex flex-wrap gap-2">
        <button type="button" className={primaryClass} disabled={busy || score === ""}
          onClick={() => void run(() => gradeHomework(task.id, studentId, Number(score), comment))}>
          {t("Save grade")}
        </button>
        <input aria-label={t("Why it goes back")} placeholder={t("Why it goes back")} className={`${inputClass} w-56`} value={reason} onChange={(e) => setReason(e.target.value)} />
        <button type="button" className={actionClass} disabled={busy || !reason.trim()}
          onClick={() => void run(() => sendBackHomework(task.id, studentId, reason))}>
          {t("Send back")}
        </button>
      </div>
      {error && <p role="alert" className="text-[12px] text-red-600">{error}</p>}
    </div>
  );
}

function StudentView() {
  const { t } = useTranslation();
  const courseParam = useSearchParams().get("course");
  const [tasks, setTasks] = useState<AssignedTask[] | null>(null);
  const [selected, setSelected] = useState<AssignedTask | null>(null);
  const [error, setError] = useState("");
  const refresh = useCallback(() => {
    listAssignedTasks().then(setTasks).catch((err) => setError(err instanceof Error ? t(err.message) : String(err)));
  }, [t]);
  useEffect(refresh, [refresh]);
  if (error) return <p role="alert" className="text-[12px] text-red-600">{error}</p>;
  if (!tasks) return <div aria-busy="true" className="h-40 animate-pulse rounded-xl bg-[var(--muted)]" />;
  return (
    <div className="space-y-4">
      {selected && <StudentTaskDetail key={selected.key} task={selected} onChanged={(next) => { setSelected(next); refresh(); }} />}
      <StudentCourses courseId={courseParam} onOpenTask={setSelected} />
      {tasks.length === 0 ? (
        <p className="text-[12.5px] text-[var(--muted-foreground)]">{t("Nothing to do right now.")}</p>
      ) : (
        <ul className="divide-y divide-[var(--border)] rounded-xl border border-[var(--border)]">
          {tasks.map((task) => (
            <li key={task.key}>
              <button type="button" className="flex w-full flex-wrap items-center gap-2 p-3 text-left text-[13px]" onClick={() => setSelected(task)}>
                <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${STATE_TONE[task.state]}`}>{t(STATE_LABEL[task.state])}</span>
                <span className="font-medium">{task.title}</span>
                <span className="text-[11.5px] text-[var(--muted-foreground)]">
                  {t(KIND_LABEL[task.kind])}{task.due_at ? ` · ${t("Due {{date}}", { date: formatWhen(task.due_at) })}` : ""}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
