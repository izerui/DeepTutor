"use client";

import { useAuthStatus } from "@/hooks/useAuthStatus";

import StudentHome from "./StudentHome";
import { AdminHome, TeacherWorkbench } from "./Workbench";

/** Home: students see their blocks; teachers a workbench; administrators the ways in (batch 3 §5). */
export default function HomeRoute() {
  const { isAdmin, isTeacher, loading, enabled } = useAuthStatus();
  if (loading) return <div aria-busy="true" className="m-8 h-40 animate-pulse rounded-xl bg-[var(--muted)]" />;
  return (
    <main className="h-full overflow-y-auto">
      {/* Without sign-in (one person on their own machine) there is no school to run:
          the learner's home, not the administrator's. */}
      {!enabled ? <StudentHome /> : isAdmin ? <AdminHome /> : isTeacher ? <TeacherWorkbench /> : <StudentHome />}
    </main>
  );
}
