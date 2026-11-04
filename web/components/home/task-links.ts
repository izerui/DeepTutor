import { masteryTopicRoute } from "@/lib/learning-routes";

/**
 * Where ``/start?task=<key>`` (a task notification) leads a teacher or an
 * administrator (traceability #7): their own task opens in the editor; a task
 * of a course they joined opens under "Courses I joined". Older keys naming a
 * practice set or a path open that directly.
 */
export function authorTaskHref(key: string, own: boolean): string {
  const [prefix, rest] = key.includes(":") ? [key.slice(0, key.indexOf(":")), key.slice(key.indexOf(":") + 1)] : ["", key];
  if (prefix === "practice_set") return `/practice-sets?set=${encodeURIComponent(rest)}`;
  if (prefix === "mastery_path") return masteryTopicRoute(`assigned_${rest}`, "");
  return own ? `/tasks?task=${encodeURIComponent(key)}` : `/tasks?tab=joined&task=${encodeURIComponent(key)}`;
}

/** Older task keys that name a practice set or a path rather than a task. */
export const isContentKey = (key: string) => key.startsWith("practice_set:") || key.startsWith("mastery_path:");
