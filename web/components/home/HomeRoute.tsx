"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { useAuthStatus } from "@/hooks/useAuthStatus";
import { listMyTasks, type MyTask } from "@/lib/tasks-api";

import StudentHome from "./StudentHome";

/** Until the teacher workbench (batch 3): what waits for grading, and the ways in. */
function TeacherHome() {
  const { t } = useTranslation();
  const [tasks, setTasks] = useState<MyTask[] | null>(null);
  useEffect(() => {
    listMyTasks().then(setTasks).catch(() => setTasks([]));
  }, []);
  const toGrade = (tasks ?? []).reduce((sum, task) => sum + task.to_grade, 0);
  return (
    <div className="mx-auto max-w-5xl space-y-6 px-6 py-8">
      <h1 className="font-serif text-[27px] text-[var(--foreground)]">{t("Home")}</h1>
      <div className="grid gap-3 sm:grid-cols-3">
        <Link href="/tasks" className="rounded-xl border border-[var(--border)] p-4 hover:border-[var(--ring)]">
          <span className="block text-[13px] font-medium">{t("Tasks")}</span>
          <span className="text-[12px] text-[var(--muted-foreground)]">
            {tasks === null ? "…" : t("{{count}} to grade", { count: toGrade })}
          </span>
        </Link>
        <Link href="/classes" className="rounded-xl border border-[var(--border)] p-4 hover:border-[var(--ring)]">
          <span className="block text-[13px] font-medium">{t("Classes")}</span>
        </Link>
        <Link href="/chat" className="rounded-xl border border-[var(--border)] p-4 hover:border-[var(--ring)]">
          <span className="block text-[13px] font-medium">{t("Start a conversation")}</span>
        </Link>
      </div>
    </div>
  );
}

/** Home (spec 2026-11-03 batch 1 §2): students see their three blocks; teachers a short summary. */
export default function HomeRoute() {
  const { isAdmin, isTeacher, loading } = useAuthStatus();
  if (loading) return <div aria-busy="true" className="m-8 h-40 animate-pulse rounded-xl bg-[var(--muted)]" />;
  return (
    <main className="h-full overflow-y-auto">{isAdmin || isTeacher ? <TeacherHome /> : <StudentHome />}</main>
  );
}
