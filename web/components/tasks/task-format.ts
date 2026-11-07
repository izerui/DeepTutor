import { bookRoute, masteryTopicRoute } from "@/lib/learning-routes";
import type { AssignedTask, TaskKind, TaskState } from "@/lib/tasks-api";

export const KIND_LABEL: Record<TaskKind, string> = {
  mastery_path: "Mastery Path",
  practice_set: "Practice set",
  book: "Reading",
  note: "Note",
};

export const STATE_LABEL: Record<TaskState, string> = {
  open: "To do",
  completed: "Done",
  closed: "Closed",
};

export const STATE_TONE: Record<TaskState, string> = {
  open: "bg-sky-500/10 text-sky-700 dark:text-sky-300",
  completed: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  closed: "bg-[var(--muted)] text-[var(--muted-foreground)]",
};

export const formatWhen = (timestamp: number | null | undefined) =>
  timestamp ? new Date(timestamp * 1000).toLocaleString() : "";

/** Where a task's "go do it" button leads; notes are done in place. */
export function targetHref(task: Pick<AssignedTask, "kind" | "target_id" | "settings">): string | null {
  switch (task.kind) {
    case "practice_set":
      return `/practice-sets?set=${encodeURIComponent(task.target_id)}`;
    case "mastery_path":
      // A learner studies their own copy of an author path (``assigned_<template>``).
      return masteryTopicRoute(`assigned_${task.target_id}`, "");
    case "book":
      return bookRoute(`book:${task.target_id}`, task.settings.page_ids?.[0] ?? null, "");
    default:
      return null;
  }
}
