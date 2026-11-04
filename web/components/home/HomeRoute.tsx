"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect } from "react";

import { useAuthStatus } from "@/hooks/useAuthStatus";
import { listMyTasks } from "@/lib/tasks-api";

import StudentHome from "./StudentHome";
import { authorTaskHref, isContentKey } from "./task-links";
import { AdminHome, TeacherWorkbench } from "./Workbench";

function AuthorTaskRedirect({ task }: { task: string }) {
  const router = useRouter();
  useEffect(() => {
    let alive = true;
    if (isContentKey(task)) {
      router.replace(authorTaskHref(task, false));
      return;
    }
    listMyTasks()
      .then((rows) => rows.some((row) => row.id === task))
      .catch(() => false)
      .then((own) => alive && router.replace(authorTaskHref(task, own)));
    return () => {
      alive = false;
    };
  }, [router, task]);
  return <div aria-busy="true" className="m-8 h-40 animate-pulse rounded-xl bg-[var(--muted)]" />;
}

/** Home: students see their blocks; teachers a workbench; administrators the ways in (batch 3 §5). */
export default function HomeRoute() {
  const { isAdmin, isTeacher, loading, enabled } = useAuthStatus();
  const task = useSearchParams()?.get("task") ?? null;
  if (loading) return <div aria-busy="true" className="m-8 h-40 animate-pulse rounded-xl bg-[var(--muted)]" />;
  const author = enabled && (isAdmin || isTeacher);
  if (author && task) return <AuthorTaskRedirect task={task} />;
  return (
    <main className="h-full overflow-y-auto">
      {/* Without sign-in (one person on their own machine) there is no school to run:
          the learner's home, not the administrator's. */}
      {!enabled ? <StudentHome /> : isAdmin ? <AdminHome /> : isTeacher ? <TeacherWorkbench /> : <StudentHome />}
    </main>
  );
}
