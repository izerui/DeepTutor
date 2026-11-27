"use client";

import dynamic from "next/dynamic";
import type { ReactNode } from "react";

const CalendarRuntime = dynamic(() => import("./CalendarRuntime"), { ssr: false });

/** Reminders load in the browser without delaying the workspace's first render. */
export default function CalendarRuntimeBoundary({ children }: { children: ReactNode }) {
  return <>{children}<CalendarRuntime>{null}</CalendarRuntime></>;
}
