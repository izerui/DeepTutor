"use client";

import { ArrowDown, ArrowUp, Plus, X } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { actionClass, inputClass, primaryClass } from "@/components/common/form-styles";
import { SourceBadge } from "@/components/common/SourceBadge";
import { AnnouncementsPanel } from "@/components/catalog/Announcements";
import { ListingPanel } from "@/components/catalog/ListingPanel";
import { JOIN_ONLY_SCOPE } from "@/lib/catalog-api";
import {
  addCourseTask,
  archiveCourse,
  createCourse,
  deleteCourse,
  getCourse,
  handOverCourse,
  listAssignedCourses,
  listMyCourses,
  listMyTasks,
  publishCourse,
  updateCourse,
  type AssignedCourse,
  type AssignedTask,
  type CourseDetail,
  type CourseSummary,
  type MyTask,
  type TaskKind,
} from "@/lib/tasks-api";

import { HandOverPanel } from "./HandOver";
import { KIND_LABEL, STATE_LABEL, STATE_TONE } from "./task-format";

const STATUS_LABEL = { draft: "Draft", published: "Published", archived: "Withdrawn" } as const;
const errorText = (err: unknown, t: (key: string) => string) => (err instanceof Error ? t(err.message) : String(err));

/** One's own courses: tasks in an order (spec 2026-11-03 batch 2 §5). */
export function CourseAuthorView({ isAdmin, onOpenTask }: { isAdmin: boolean; onOpenTask: (taskId: string) => void }) {
  const { t } = useTranslation();
  const [courses, setCourses] = useState<CourseSummary[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [error, setError] = useState("");

  const refresh = useCallback(() => {
    listMyCourses().then(setCourses).catch((err) => setError(errorText(err, t)));
  }, [t]);
  useEffect(refresh, [refresh]);

  return (
    <div className="grid gap-5 lg:grid-cols-[300px_1fr]">
      <aside className="space-y-3">
        <form className="flex gap-2" onSubmit={(event) => {
          event.preventDefault();
          createCourse(title.trim())
            .then((created) => { setTitle(""); setSelected(created.id); refresh(); })
            .catch((err) => setError(errorText(err, t)));
        }}>
          <input className={inputClass} value={title} maxLength={200} placeholder={t("New course title")}
            onChange={(e) => setTitle(e.target.value)} />
          <button type="submit" className={primaryClass} disabled={!title.trim()} aria-label={t("Create")}>
            <Plus size={14} />
          </button>
        </form>
        {error && <p role="alert" className="text-[12px] text-[var(--destructive)]">{error}</p>}
        {courses === null ? (
          <div aria-busy="true" className="h-24 animate-pulse rounded-xl bg-[var(--muted)]" />
        ) : courses.length === 0 ? (
          <p className="text-[12.5px] text-[var(--muted-foreground)]">
            {t("No courses yet. A course lays out a term's tasks in order.")}
          </p>
        ) : (
          <ul className="space-y-1">
            {courses.map((course) => (
              <li key={course.id}>
                <button type="button" onClick={() => setSelected(course.id)}
                  className={`w-full rounded-lg px-3 py-2 text-left text-[13px] hover:bg-[var(--muted)] ${selected === course.id ? "bg-[var(--muted)]" : ""}`}>
                  <span className="block truncate font-medium text-[var(--foreground)]">{course.title}</span>
                  <span className="text-[11.5px] text-[var(--muted-foreground)]">
                    {t(STATUS_LABEL[course.status])} · {t("{{count}} tasks", { count: course.task_count })}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </aside>
      <section className="min-w-0">
        {selected ? (
          <CourseEditor key={selected} courseId={selected} isAdmin={isAdmin} onChanged={refresh}
            onOpenTask={onOpenTask} onDeleted={() => { setSelected(null); refresh(); }} />
        ) : (
          <div className="flex h-40 items-center justify-center rounded-xl border border-dashed border-[var(--border)] text-[13px] text-[var(--muted-foreground)]">
            {t("Pick a course or create one.")}
          </div>
        )}
      </section>
    </div>
  );
}

function CourseEditor({
  courseId,
  isAdmin,
  onChanged,
  onDeleted,
  onOpenTask,
}: {
  courseId: string;
  isAdmin: boolean;
  onChanged: () => void;
  onDeleted: () => void;
  onOpenTask: (taskId: string) => void;
}) {
  const { t } = useTranslation();
  const [course, setCourse] = useState<CourseDetail | null>(null);
  const [mine, setMine] = useState<MyTask[]>([]);
  const [pick, setPick] = useState("");
  const [kind, setKind] = useState<TaskKind>("practice_set");
  const [newTitle, setNewTitle] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getCourse(courseId).then(setCourse).catch((err) => setError(errorText(err, t)));
    listMyTasks().then(setMine).catch(() => setMine([]));
  }, [courseId, t]);

  const run = async (action: () => Promise<CourseDetail | unknown>) => {
    setBusy(true);
    setError("");
    try {
      const next = await action();
      if (next && typeof next === "object" && "tasks" in next) setCourse(next as CourseDetail);
      else setCourse(await getCourse(courseId));
      onChanged();
    } catch (err) {
      setError(errorText(err, t));
    } finally {
      setBusy(false);
    }
  };

  if (!course) return error ? <p role="alert" className="text-[12px] text-red-600">{error}</p> : null;
  const editable = course.status !== "archived";
  const ids = course.tasks.map((task) => task.id);
  const move = (index: number, by: number) => {
    const next = [...ids];
    [next[index], next[index + by]] = [next[index + by], next[index]];
    void run(() => updateCourse(course.id, { task_ids: next }));
  };
  const kinds: TaskKind[] = ["practice_set", "mastery_path", "book", "note"];
  const addable = mine.filter((task) => task.status !== "archived" && !ids.includes(task.id));

  return (
    <fieldset disabled={busy} className="space-y-4 rounded-xl border border-[var(--border)] p-4" data-testid="course-editor">
      <div className="space-y-1">
        <input aria-label={t("Course title")} className={`${inputClass} text-[15px] font-semibold`} defaultValue={course.title}
          disabled={!editable}
          onBlur={(e) => e.target.value.trim() && e.target.value !== course.title && void run(() => updateCourse(course.id, { title: e.target.value.trim() }))} />
        <span className="text-[12px] text-[var(--muted-foreground)]">{t(STATUS_LABEL[course.status])}</span>
      </div>
      <textarea aria-label={t("Course description")} placeholder={t("Course description")} rows={3} className={inputClass}
        defaultValue={course.description} disabled={!editable}
        onBlur={(e) => e.target.value !== course.description && void run(() => updateCourse(course.id, { description: e.target.value }))} />

      <div className="space-y-2">
        <h3 className="text-[13px] font-medium">{t("Tasks in order")}</h3>
        <p className="text-[11.5px] text-[var(--muted-foreground)]">
          {t("The order is a suggestion; learners can do the tasks in any order.")}
        </p>
        {course.tasks.length === 0 ? (
          <p className="text-[12.5px] text-[var(--muted-foreground)]">{t("No tasks yet.")}</p>
        ) : (
          <ol className="space-y-1">
            {course.tasks.map((task, index) => (
              <li key={task.id} className="flex items-center gap-2 rounded-lg border border-[var(--border)] px-2 py-1.5 text-[12.5px]">
                <span className="w-5 text-right tabular-nums text-[var(--muted-foreground)]">{index + 1}.</span>
                <button type="button" className="min-w-0 flex-1 truncate text-left font-medium hover:underline" onClick={() => onOpenTask(task.id)}>
                  {task.title}
                </button>
                <span className="text-[11px] text-[var(--muted-foreground)]">
                  {t(KIND_LABEL[task.kind])} · {t(STATUS_LABEL[task.status])}
                </span>
                {editable && (
                  <>
                    <button type="button" aria-label={t("Move up")} disabled={index === 0} onClick={() => move(index, -1)} className="disabled:opacity-30">
                      <ArrowUp size={13} />
                    </button>
                    <button type="button" aria-label={t("Move down")} disabled={index === ids.length - 1} onClick={() => move(index, 1)} className="disabled:opacity-30">
                      <ArrowDown size={13} />
                    </button>
                    <button type="button" aria-label={t("Remove from course")}
                      onClick={() => void run(() => updateCourse(course.id, { task_ids: ids.filter((x) => x !== task.id) }))}>
                      <X size={13} />
                    </button>
                  </>
                )}
              </li>
            ))}
          </ol>
        )}
        {editable && (
          <form className="flex flex-wrap items-center gap-2 rounded-lg bg-[var(--muted)]/40 p-2" data-testid="course-new-task" onSubmit={(event) => {
            event.preventDefault();
            const name = newTitle.trim();
            setNewTitle("");
            void run(() => addCourseTask(course.id, kind, name));
          }}>
            <select aria-label={t("Kind of task")} className={`${inputClass} w-auto`} value={kind} onChange={(e) => setKind(e.target.value as TaskKind)}>
              {kinds.map((k) => <option key={k} value={k}>{t(KIND_LABEL[k])}</option>)}
            </select>
            <input className={`${inputClass} w-56`} value={newTitle} maxLength={200} placeholder={t("New task title")}
              onChange={(e) => setNewTitle(e.target.value)} />
            <button type="submit" className={primaryClass} disabled={!newTitle.trim()}>{t("Add new task")}</button>
          </form>
        )}
        {editable && (
          <div className="flex flex-wrap gap-2">
            <select aria-label={t("Add an existing task")} className={`${inputClass} w-auto`} value={pick} onChange={(e) => setPick(e.target.value)}>
              <option value="">{t("Add an existing task…")}</option>
              {addable.map((task) => <option key={task.id} value={task.id}>{task.title}</option>)}
            </select>
            <button type="button" className={actionClass} disabled={!pick}
              onClick={() => { const id = pick; setPick(""); void run(() => updateCourse(course.id, { task_ids: [...ids, id] })); }}>
              {t("Add")}
            </button>
          </div>
        )}
      </div>

      {course.status === "draft" && (
        <>
          <p className="text-[11.5px] text-[var(--muted-foreground)]">
            {t("Publishing also publishes its draft tasks. Nobody receives the course until they join from the learning market; list it here once it is published.")}
          </p>
        </>
      )}
      {course.status === "published" && <ListingPanel kind="course" itemId={course.id} onChanged={onChanged} />}
      {course.status === "published" && (
        <>
          {/* Platform batch 3 §1, §3: how its learners are doing, and announcements to them. */}
          <Link href={`/reports?kind=course&id=${encodeURIComponent(course.id)}`} className={actionClass}>
            {t("Learning report")}
          </Link>
          <AnnouncementsPanel kind="course" itemId={course.id} canPost />
        </>
      )}
      <div className="flex flex-wrap gap-2">
        {course.status === "draft" && (
          <button type="button" className={primaryClass} disabled={course.tasks.length === 0}
            onClick={() => void run(() => publishCourse(course.id, JOIN_ONLY_SCOPE))}>
            {t("Publish")}
          </button>
        )}
        {course.status === "published" && (
          <button type="button" className={actionClass}
            onClick={() => window.confirm(t("Withdraw this course? Its tasks stay with the learners.")) && void run(() => archiveCourse(course.id))}>
            {t("Withdraw")}
          </button>
        )}
        {course.status === "draft" && (
          <button type="button" className={actionClass}
            onClick={() => window.confirm(t("Delete this course? Its tasks are kept.")) &&
              void deleteCourse(course.id).then(onDeleted).catch((err) => setError(errorText(err, t)))}>
            {t("Delete")}
          </button>
        )}
      </div>
      {/* Traceability #5: what an administrator holds goes to a learner. */}
      {isAdmin && <HandOverPanel handOver={(learnerId) => handOverCourse(course.id, learnerId)} onDone={onDeleted} />}
      {error && <p role="alert" className="text-[12px] text-red-600">{error}</p>}
    </fieldset>
  );
}

/** The courses one joined, each with its tasks in order. */
export function StudentCourses({
  courseId,
  onOpenTask,
}: {
  /** ``?course=`` (a notification's link): that course is shown open. */
  courseId: string | null;
  onOpenTask: (task: AssignedTask) => void;
}) {
  const { t } = useTranslation();
  const [courses, setCourses] = useState<AssignedCourse[]>([]);
  const [open, setOpen] = useState<string | null>(courseId);
  // A new ``?course=`` opens that course, also when already on this page.
  const [seen, setSeen] = useState(courseId);
  if (courseId !== seen) {
    setSeen(courseId);
    setOpen(courseId);
  }
  useEffect(() => {
    listAssignedCourses().then(setCourses).catch(() => setCourses([]));
  }, []);
  if (!courses.length) return null;
  return (
    <section className="space-y-2" data-testid="student-courses">
      <h2 className="text-[13px] font-semibold">{t("Courses")}</h2>
      {courses.map((course) => {
        const done = course.tasks.filter((task) => task.state === "completed").length;
        return (
          <div key={course.id} className="rounded-xl border border-[var(--border)] p-3">
            <button type="button" className="flex w-full flex-wrap items-center gap-2 text-left" onClick={() => setOpen(open === course.id ? null : course.id)}>
              <span className="text-[14px] font-medium">{course.title}</span>
              <SourceBadge provenance={course.provenance} />
              <span className="text-[11.5px] text-[var(--muted-foreground)]">
                {t("{{done}} / {{total}} done", { done, total: course.tasks.length })}
              </span>
            </button>
            {open === course.id && (
              <div className="mt-2 space-y-2">
                {course.description && <p className="whitespace-pre-line text-[12.5px]">{course.description}</p>}
                <AnnouncementsPanel kind="course" itemId={course.id} />
                <ol className="space-y-1">
                  {course.tasks.map((task, index) => (
                    <li key={task.key}>
                      <button type="button" className="flex w-full flex-wrap items-center gap-2 rounded-lg px-2 py-1 text-left text-[12.5px] hover:bg-[var(--muted)]"
                        onClick={() => onOpenTask(task)}>
                        <span className="w-5 text-right tabular-nums text-[var(--muted-foreground)]">{index + 1}.</span>
                        <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${STATE_TONE[task.state]}`}>{t(STATE_LABEL[task.state])}</span>
                        <span className="font-medium">{task.title}</span>
                      </button>
                    </li>
                  ))}
                </ol>
              </div>
            )}
          </div>
        );
      })}
    </section>
  );
}

/** On the learner's own course shelf: the courses they joined, kept apart from their own. */
export function TeacherCoursesLink() {
  const { t } = useTranslation();
  const [courses, setCourses] = useState<AssignedCourse[]>([]);
  useEffect(() => {
    listAssignedCourses().then(setCourses).catch(() => setCourses([]));
  }, []);
  if (!courses.length) return null;
  return (
    <section className="space-y-2" data-testid="teacher-courses">
      <h2 className="text-[13px] font-semibold">{t("Courses I joined")}</h2>
      <ul className="flex flex-wrap gap-2">
        {courses.map((course) => (
          <li key={course.id}>
            <Link href={`/tasks?course=${encodeURIComponent(course.id)}`}
              className="inline-flex items-center gap-2 rounded-xl border border-[var(--border)] px-3 py-2 text-[13px] hover:border-[var(--ring)]">
              {course.title}
              <SourceBadge provenance={course.provenance} />
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
