"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { primaryClass } from "@/components/common/form-styles";
import { useAuthStatus } from "@/hooks/useAuthStatus";
import { getAssignedTask, listAssignedTasks, type AssignedCourse, type AssignedTask } from "@/lib/tasks-api";

import { CourseAuthorView, LearnerCourses } from "./Courses";
import { LearnerLessonDetail } from "./LearnerLessonDetail";
import { KIND_LABEL, STATE_LABEL, STATE_TONE } from "./task-format";

/**
 * Courses (spec 2026-11-06 §1; the route stays ``/tasks``): the courses one
 * joined and the courses one made. A course's items are its lessons and are
 * seen inside it; there is no separate list of every lesson.
 */
export default function TasksHub() {
  const { t } = useTranslation();
  const { isAdmin, loading } = useAuthStatus();
  if (loading) return <div aria-busy="true" className="h-40 animate-pulse rounded-xl bg-[var(--muted)]" />;
  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-serif text-[27px] text-[var(--foreground)]">{t("My courses")}</h1>
        <p className="mt-1 text-[13px] text-[var(--muted-foreground)]">
          {t("The courses you are learning and the courses you created.")}
        </p>
      </header>
      <CourseTabs isAdmin={isAdmin} />
    </div>
  );
}

type Tab = "joined" | "courses";

/**
 * Where the links lead: ``?course=`` and ``?tab=joined`` (``&task=``) open
 * what one joined; ``?tab=courses`` (and the older ``?tab=tasks``) one's own
 * courses; ``?task=`` alone one's own lesson inside its course, or — when it
 * is not one's own — that lesson among what one joined.
 */
function tabFor(params: URLSearchParams | null, isAdmin: boolean): Tab {
  const tab = params?.get("tab");
  if (params?.get("course") || tab === "joined") return "joined";
  if (tab === "courses" || tab === "tasks" || params?.get("task") || isAdmin) return "courses";
  return "joined";
}

function CourseTabs({ isAdmin }: { isAdmin: boolean }) {
  const { t } = useTranslation();
  const params = useSearchParams();
  const taskParam = params.get("task");
  // Set once ``?task=`` proved not to be one's own: it is looked for among what one joined.
  const [notMine, setNotMine] = useState(false);
  const ownLesson = params.get("tab") === "joined" || notMine ? null : taskParam;
  const [tab, setTab] = useState<Tab>(() => tabFor(params, isAdmin));
  // A new link while already here (a notification) moves to where it leads.
  const query = params.toString();
  const [seen, setSeen] = useState(query);
  if (query !== seen) {
    setSeen(query);
    setNotMine(false);
    setTab(tabFor(params, isAdmin));
  }
  const tabClass = (on: boolean) =>
    `rounded-lg px-3 py-1.5 text-[13px] ${on ? "bg-[var(--muted)] font-medium text-[var(--foreground)]" : "text-[var(--muted-foreground)] hover:bg-[var(--muted)]"}`;
  return (
    <div className="space-y-4">
      <div role="tablist" className="flex gap-1">
        <button type="button" role="tab" aria-selected={tab === "joined"} className={tabClass(tab === "joined")} onClick={() => setTab("joined")}>
          {t("Courses tab: learning")}
        </button>
        <button type="button" role="tab" aria-selected={tab === "courses"} className={tabClass(tab === "courses")} onClick={() => setTab("courses")}>
          {t("Courses tab: created")}
        </button>
      </div>
      {tab === "courses" ? (
        <CourseAuthorView key={ownLesson ?? ""} isAdmin={isAdmin} initialLesson={ownLesson}
          onLessonMissing={() => { setNotMine(true); setTab("joined"); }} />
      ) : (
        <JoinedView />
      )}
    </div>
  );
}

/** What one joined: each course with its lessons; lessons outside any course (handed over, older) below. */
function JoinedView() {
  const { t } = useTranslation();
  const params = useSearchParams();
  const courseParam = params.get("course");
  // ``?task=`` (a notification's link) opens that lesson, its course open beside it.
  const taskParam = params.get("task");
  const [tasks, setTasks] = useState<AssignedTask[] | null>(null);
  const [inCourses, setInCourses] = useState<Set<string> | null>(null);
  const [selected, setSelected] = useState<AssignedTask | null>(null);
  const [error, setError] = useState("");
  const [taskError, setTaskError] = useState("");
  // Bumped when a lesson changes, so the course cards recount what is done.
  const [changes, setChanges] = useState(0);
  const refresh = useCallback(() => {
    listAssignedTasks().then(setTasks).catch((err) => setError(err instanceof Error ? t(err.message) : String(err)));
  }, [t]);
  useEffect(refresh, [refresh]);
  useEffect(() => {
    if (!taskParam) return;
    let alive = true;
    getAssignedTask(taskParam)
      .then((task) => alive && (setSelected(task), setTaskError("")))
      .catch(() => alive && setTaskError(t("This lesson is no longer available.")));
    return () => {
      alive = false;
    };
  }, [taskParam, t]);
  const onCourses = useCallback(
    (courses: AssignedCourse[]) => setInCourses(new Set(courses.flatMap((course) => course.tasks.map((task) => task.key)))),
    [],
  );
  if (error) return <p role="alert" className="text-[12px] text-red-600">{error}</p>;
  if (!tasks) return <div aria-busy="true" className="h-40 animate-pulse rounded-xl bg-[var(--muted)]" />;
  const loose = inCourses ? tasks.filter((task) => !inCourses.has(task.key)) : [];
  return (
    <div className="space-y-4">
      {taskError && <p role="alert" className="text-[12px] text-red-600">{taskError}</p>}
      {selected && <LearnerLessonDetail key={selected.key} task={selected} onChanged={(next) => { setSelected(next); refresh(); setChanges((n) => n + 1); }} />}
      <LearnerCourses courseId={courseParam} openTaskKey={taskParam} onOpenTask={setSelected} reload={changes} onLoaded={onCourses} />
      {inCourses !== null && inCourses.size === 0 && loose.length === 0 && (
        // Polish batch §4: nothing joined yet: lead to the market.
        <div className="space-y-2 rounded-xl border border-dashed border-[var(--border)] p-6 text-center" data-testid="learning-courses-empty">
          <p className="text-[13px] text-[var(--muted-foreground)]">{t("Courses you join from the learning market appear here.")}</p>
          <Link href="/market?kind=course" className={primaryClass}>{t("Find a course in the market")}</Link>
        </div>
      )}
      {loose.length > 0 && (
        <section className="space-y-2" aria-label={t("Other lessons")}>
          <h2 className="text-[13px] font-semibold">{t("Other lessons")}</h2>
          <ul className="divide-y divide-[var(--border)] rounded-xl border border-[var(--border)]">
            {loose.map((task) => (
              <li key={task.key}>
                <button type="button" className="flex w-full flex-wrap items-center gap-2 p-3 text-left text-[13px]" onClick={() => setSelected(task)}>
                  <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${STATE_TONE[task.state]}`}>{t(STATE_LABEL[task.state])}</span>
                  <span className="font-medium">{task.title}</span>
                  <span className="text-[11.5px] text-[var(--muted-foreground)]">{t(KIND_LABEL[task.kind])}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
