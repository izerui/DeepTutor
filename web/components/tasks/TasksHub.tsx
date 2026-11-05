"use client";

import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { actionClass, inputClass, primaryClass } from "@/components/common/form-styles";
import { useAuthStatus } from "@/hooks/useAuthStatus";
import { bookApi } from "@/lib/book-api";
import type { Book, Chapter } from "@/lib/book-types";
import { listMySets, type PracticeSetSummary } from "@/lib/practice-sets-api";
import {
  archiveTask,
  deleteTask,
  getAssignedTask,
  getTask,
  handOverTask,
  listAssignedTasks,
  listMyTasks,
  publishTask,
  updateTask,
  type AssignedTask,
  type MyTask,
  type TaskDetail,
} from "@/lib/tasks-api";
import { listTeacherTemplates, type TeacherTemplate } from "@/lib/teaching-api";

import { CourseAuthorView, StudentCourses } from "./Courses";
import { HandOverPanel } from "./HandOver";
import { StudentTaskDetail } from "./StudentTaskDetail";
import { KIND_LABEL, STATE_LABEL, STATE_TONE } from "./task-format";

const STATUS_LABEL = { draft: "Draft", published: "Published", archived: "Withdrawn" } as const;

/**
 * Tasks (spec 2026-11-03 batch 1): teachers and administrators assign them and
 * see who has done them; students see every task they received, by state.
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
            ? t("Edit the tasks of your courses and see who has done them.")
            : t("Tasks from the courses you joined.")}
        </p>
      </header>
      {isAdmin || isTeacher ? <AuthorTabs isAdmin={isAdmin} /> : <StudentView />}
    </div>
  );
}

/**
 * Tasks grouped by course, the courses themselves (platform batch 2 §5: tasks
 * live in courses), and what the author joined to learn (§6).
 */
function AuthorTabs({ isAdmin }: { isAdmin: boolean }) {
  const { t } = useTranslation();
  // ``?course=`` (a notification about a joined course) opens what the author joined.
  const params = useSearchParams();
  const courseParam = params.get("course");
  // ``?tab=courses`` returns from a course's learning report; ``?tab=joined&task=``
  // opens a task of a course one joined (traceability #7).
  const tabParam = params.get("tab");
  const [tab, setTab] = useState<"tasks" | "courses" | "joined">(
    courseParam || tabParam === "joined" ? "joined" : tabParam === "courses" ? "courses" : "tasks",
  );
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
        <button type="button" role="tab" aria-selected={tab === "joined"} className={tabClass(tab === "joined")} onClick={() => setTab("joined")}>
          {t("Courses I joined")}
        </button>
      </div>
      {tab === "tasks" ? (
        <AuthorView key={openTask ?? ""} initialTask={openTask} onGoToCourses={() => setTab("courses")} />
      ) : tab === "courses" ? (
        <CourseAuthorView isAdmin={isAdmin} onOpenTask={(id) => { setOpenTask(id); setTab("tasks"); }} />
      ) : (
        <StudentView />
      )}
    </div>
  );
}

/** The author's tasks, by the course that holds them; old loose tasks under "Other tasks". */
export function groupTasksByCourse(tasks: MyTask[]): { id: string; title: string; tasks: MyTask[] }[] {
  const groups = new Map<string, { id: string; title: string; tasks: MyTask[] }>();
  for (const task of tasks) {
    const key = task.course_id || "";
    if (!groups.has(key)) groups.set(key, { id: key, title: task.course_title || "", tasks: [] });
    groups.get(key)!.tasks.push(task);
  }
  const loose = groups.get("");
  groups.delete("");
  return [...[...groups.values()].sort((a, b) => a.title.localeCompare(b.title)), ...(loose ? [loose] : [])];
}

function AuthorView({ initialTask = null, onGoToCourses }: { initialTask?: string | null; onGoToCourses: () => void }) {
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
  const [error, setError] = useState("");

  const refresh = useCallback(() => {
    listMyTasks()
      .then(setTasks)
      .catch((err) => setError(err instanceof Error ? t(err.message) : String(err)));
  }, [t]);
  useEffect(refresh, [refresh]);

  return (
    <div className="grid gap-5 lg:grid-cols-[300px_1fr]">
      <aside className="space-y-3">
        <div className="space-y-2 rounded-xl border border-dashed border-[var(--border)] p-3" data-testid="tasks-in-courses-hint">
          <p className="text-[12.5px] text-[var(--muted-foreground)]">{t("Tasks are added inside a course.")}</p>
          <button type="button" className={actionClass} onClick={onGoToCourses}>{t("Go to courses")}</button>
        </div>
        {error && <p role="alert" className="text-[12px] text-[var(--destructive)]">{error}</p>}
        {tasks === null ? (
          <div aria-busy="true" className="h-24 animate-pulse rounded-xl bg-[var(--muted)]" />
        ) : tasks.length === 0 ? (
          <p className="text-[12.5px] text-[var(--muted-foreground)]">{t("No tasks yet.")}</p>
        ) : (
          groupTasksByCourse(tasks).map((group) => (
            <section key={group.id || "other"} aria-label={group.id ? group.title : t("Other tasks")} className="space-y-1">
              <h2 className="px-1 text-[12px] font-semibold text-[var(--muted-foreground)]">
                {group.id ? group.title : t("Other tasks")}
              </h2>
              <ul className="space-y-1">
                {group.tasks.map((task) => (
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
                        {!task.target_available && ` · ${t("Content unavailable")}`}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          ))
        )}
      </aside>
      <section className="min-w-0">
        {selected ? (
          <TaskEditor key={selected} taskId={selected} onChanged={refresh} onDeleted={() => { setSelected(null); refresh(); }} />
        ) : (
          <div className="flex h-40 items-center justify-center rounded-xl border border-dashed border-[var(--border)] text-[13px] text-[var(--muted-foreground)]">
            {t("Pick a task to edit it.")}
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
  return null;
}

function TaskEditor({ taskId, onChanged, onDeleted }: { taskId: string; onChanged: () => void; onDeleted: () => void }) {
  const { t } = useTranslation();
  const { isAdmin } = useAuthStatus();
  const [task, setTask] = useState<TaskDetail | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

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
      {draft ? (
        <TargetPicker task={task} onChange={(fields) => save(fields)} />
      ) : task.target_title ? (
        <p className="text-[12.5px]">{t("Asks for: {{title}}", { title: task.target_title })}</p>
      ) : null}

      {draft && !task.course_id && (
        <p className="text-[11.5px] text-[var(--muted-foreground)]">
          {t("This task is in no course. Add it to a course; it is published with the course.")}
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
      {/* Traceability #5: a task in no course goes on its own to a teacher. */}
      {isAdmin && !task.course_id && (
        <HandOverPanel handOver={(teacherId) => handOverTask(task.id, teacherId)} onDone={onDeleted} />
      )}
      {error && <p role="alert" className="text-[12px] text-red-600">{error}</p>}

      {!draft && <StudentsTable task={task} />}
    </fieldset>
  );
}

function StudentsTable({ task }: { task: TaskDetail }) {
  const { t } = useTranslation();
  return (
    <section className="space-y-2">
      <h3 className="text-[13px] font-semibold">{t("Students")}</h3>
      {task.students.length === 0 ? (
        <p className="text-[12.5px] text-[var(--muted-foreground)]">{t("No students receive this yet.")}</p>
      ) : (
        <ul className="divide-y divide-[var(--border)] rounded-xl border border-[var(--border)]">
          {task.students.map((student) => (
            <li key={student.id} className="flex flex-wrap items-center gap-2 p-3 text-[13px]">
              <span className="font-medium">{student.username}</span>
              {!student.in_scope && <span className="text-[11.5px] text-[var(--muted-foreground)]">{t("Not in scope")}</span>}
              <span className="text-[11.5px] text-[var(--muted-foreground)]">
                {student.completed
                  ? student.self_reported ? t("Done (self-reported)") : t("Done")
                  : t("Not done")}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function StudentView() {
  const { t } = useTranslation();
  const params = useSearchParams();
  const courseParam = params.get("course");
  // ``?task=`` (a notification's link, or ``/start?task=`` for a teacher) opens that task.
  const taskParam = params.get("task");
  const [tasks, setTasks] = useState<AssignedTask[] | null>(null);
  const [selected, setSelected] = useState<AssignedTask | null>(null);
  const [error, setError] = useState("");
  const [taskError, setTaskError] = useState("");
  const refresh = useCallback(() => {
    listAssignedTasks().then(setTasks).catch((err) => setError(err instanceof Error ? t(err.message) : String(err)));
  }, [t]);
  useEffect(refresh, [refresh]);
  useEffect(() => {
    if (!taskParam) return;
    let alive = true;
    getAssignedTask(taskParam)
      .then((task) => alive && (setSelected(task), setTaskError("")))
      .catch(() => alive && setTaskError(t("This task is no longer available.")));
    return () => {
      alive = false;
    };
  }, [taskParam, t]);
  if (error) return <p role="alert" className="text-[12px] text-red-600">{error}</p>;
  if (!tasks) return <div aria-busy="true" className="h-40 animate-pulse rounded-xl bg-[var(--muted)]" />;
  return (
    <div className="space-y-4">
      {taskError && <p role="alert" className="text-[12px] text-red-600">{taskError}</p>}
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
                  {t(KIND_LABEL[task.kind])}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
