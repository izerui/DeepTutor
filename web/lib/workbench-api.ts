import { apiFetch, apiUrl } from "./api";
import type { AttentionFlag } from "./report-types";

/** The teacher's home (batch 3 §5.1); administrators get only ``role``. */
export interface Workbench {
  role: string;
  /**
   * Platform batch 3 §2: learners of this teacher's content; ``item`` is one
   * course or path they joined (its report), ``null`` on an older install.
   */
  attention?: {
    student_id: string;
    username: string;
    class_id: string;
    item?: { kind: "course" | "mastery_path"; id: string; title: string } | null;
    flags: AttentionFlag[];
  }[];
  attention_total?: number;
  classes?: { id: string; name: string; student_count: number; active_this_week: number }[];
  sent?: {
    kind: "task" | "practice_set" | "course" | "mastery_path";
    id: string;
    title: string;
    /** ``null`` where completion is not counted (courses, paths: ``total`` is how many). */
    done: number | null;
    total: number;
    updated_at: number;
  }[];
}

/** Batch 3 §8: today's AI tokens; ``limit`` 0 is no limit. */
export interface AiUsage {
  limit: number;
  used: number;
  remaining_percent: number | null;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await apiFetch(apiUrl(path), {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json", ...init?.headers } : init?.headers,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(typeof data?.detail === "string" ? data.detail : `Request failed (${res.status})`);
  }
  return (await res.json()) as T;
}

export const getWorkbench = () => call<Workbench>("/api/home/workbench");
export const getMyAiUsage = () => call<AiUsage>("/api/ai-usage/me");
export const getSchoolAiLimit = () => call<{ daily_tokens: number }>("/api/ai-usage/default").then((r) => r.daily_tokens);
export const setSchoolAiLimit = (dailyTokens: number) =>
  call<{ daily_tokens: number }>("/api/ai-usage/default", {
    method: "PUT", body: JSON.stringify({ daily_tokens: dailyTokens }),
  }).then((r) => r.daily_tokens);
/** A student's remedial questions from their mistakes (batch 3 §3.2). */
export const similarQuestions = (entryIds: number[], count: number, language: string) =>
  call<{ entry_ids: number[]; title: string }>("/api/question-notebook/remedial/similar", {
    method: "POST", body: JSON.stringify({ entry_ids: entryIds, count, language }),
  });

/**
 * Admin presets for the daily limit, shown as rough conversation counts
 * (batch 3 §2.2), reckoning about 10,000 tokens per question and answer.
 */
export const AI_LIMIT_PRESETS: { tokens: number; label: string }[] = [
  { tokens: 0, label: "No limit" },
  { tokens: 200_000, label: "About 20 conversations a day" },
  { tokens: 500_000, label: "About 50 conversations a day" },
  { tokens: 1_000_000, label: "About 100 conversations a day" },
];
