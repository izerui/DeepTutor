import { apiFetch, apiUrl } from "./api";

/** Practice sets are system-level: never workspace-scoped (spec 2026-10-29 §8). */
const BASE = "/api/practice-sets";

export type PracticeQuestionType =
  | "choice"
  | "multi_choice"
  | "concept"
  | "fill_in_blank"
  | "short_answer"
  | "written"
  | "coding";

export interface PracticeQuestion {
  question_id: string;
  question: string;
  question_type: PracticeQuestionType;
  options: Record<string, string>;
  correct_answer?: string;
  explanation?: string;
  points: number;
}

export type Reveal = "after_submit" | "after_due";

export interface PracticeSetSummary {
  id: string;
  title: string;
  description: string;
  status: "draft" | "published" | "archived";
  current_revision: number;
  due_at: number | null;
  reveal: Reveal;
  created_at: number;
  updated_at: number;
  submitted_students?: number;
}

export interface PracticeSetDraft extends PracticeSetSummary {
  questions: PracticeQuestion[];
}

export interface AssignedSet extends PracticeSetSummary {
  teacher: string;
  submissions: number;
  best_score: number | null;
  max_score: number | null;
}

export type ResultValue = "correct" | "partial" | "incorrect" | "ungraded";

export interface QuestionResult {
  question_id: string;
  result: ResultValue;
  points: number;
  max_points: number;
  feedback?: string;
  correct_answer?: string;
  explanation?: string;
}

export interface TakeView extends PracticeSetSummary {
  teacher: string;
  answers_revealed: boolean;
  questions: PracticeQuestion[];
  submissions: { id: string; revision: number; submitted_at: number; late: number; score: number; max_score: number }[];
  latest_results: QuestionResult[];
}

export interface SubmitResult {
  submission_id: string;
  score: number;
  max_score: number;
  late: boolean;
  answers_revealed: boolean;
  results: QuestionResult[];
}

export interface PracticeReport {
  set_id: string;
  title: string;
  due_at: number | null;
  students: {
    id: string;
    username: string;
    completed: boolean;
    attempts: number;
    first_score: number | null;
    best_score: number | null;
    max_score: number | null;
    minutes: number | null;
    late: boolean;
    submitted_at: number | null;
  }[];
  questions: { question_id: string; question: string; answered: number; error_rate: number | null }[];
}

export class PracticeSetsApiError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message);
    this.name = "PracticeSetsApiError";
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await apiFetch(apiUrl(`${BASE}${path}`), {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json", ...init?.headers } : init?.headers,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new PracticeSetsApiError(
      typeof data?.detail === "string" ? data.detail : `Request failed (${res.status})`,
      res.status,
    );
  }
  return (await res.json()) as T;
}

const send = (method: string, body?: unknown): RequestInit => ({
  method,
  ...(body === undefined ? {} : { body: JSON.stringify(body) }),
});
const id = (value: string) => `/${encodeURIComponent(value)}`;

export const listMySets = () => call<{ sets: PracticeSetSummary[] }>("/mine").then((r) => r.sets);
export const createSet = (title: string) => call<{ set: PracticeSetDraft }>("", send("POST", { title })).then((r) => r.set);
export const getSet = (setId: string) => call<{ set: PracticeSetDraft }>(id(setId)).then((r) => r.set);
export const updateSet = (
  setId: string,
  update: Partial<Pick<PracticeSetDraft, "title" | "description" | "questions" | "reveal">> & {
    due_at?: number | null;
  },
) => {
  const { due_at, ...rest } = update;
  const body = due_at === undefined ? rest : due_at === null ? { ...rest, clear_due: true } : { ...rest, due_at };
  return call<{ set: PracticeSetDraft }>(id(setId), send("PUT", body)).then((r) => r.set);
};
export const publishSet = (setId: string) =>
  call<{ set: PracticeSetDraft }>(`${id(setId)}/publish`, send("POST")).then((r) => r.set);
export const archiveSet = (setId: string) =>
  call<{ set: PracticeSetDraft }>(`${id(setId)}/archive`, send("POST")).then((r) => r.set);
export const deleteSet = (setId: string) => call<{ ok: boolean }>(id(setId), send("DELETE"));
export const generateQuestions = (body: {
  topic: string;
  count: number;
  kb_ref?: string | null;
  question_types?: string[];
  language?: string;
}) => call<{ questions: PracticeQuestion[] }>("/generate", send("POST", body)).then((r) => r.questions);
export const questionsFromBank = (entryIds: number[]) =>
  call<{ questions: PracticeQuestion[] }>("/from-bank", send("POST", { entry_ids: entryIds })).then((r) => r.questions);
export const getReport = (setId: string) => call<PracticeReport>(`${id(setId)}/report`);

export const listAssigned = () => call<{ sets: AssignedSet[] }>("/assigned").then((r) => r.sets);
export const takeSet = (setId: string) => call<{ set: TakeView }>(`${id(setId)}/take`).then((r) => r.set);
export const submitSet = (setId: string, answers: Record<string, string>, startedAt: number | null) =>
  call<SubmitResult>(`${id(setId)}/submit`, send("POST", { answers, started_at: startedAt }));
