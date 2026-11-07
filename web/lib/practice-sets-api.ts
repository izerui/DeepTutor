import type { Provenance } from "./provenance";
import { apiFetch, apiUrl } from "./api";
import { downloadAuthenticated } from "./authenticated-download";

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

/** Answers show right after submitting (spec 2026-11-05 §1.4). */
export type Reveal = "after_submit";

export interface PracticeSetSummary {
  id: string;
  title: string;
  description: string;
  status: "draft" | "published" | "archived";
  current_revision: number;
  reveal: Reveal;
  /** Batch 3 §6.1: at most this many attempts (``null``: no limit); which attempt counts. */
  attempts_limit?: number | null;
  score_mode?: ScoreMode;
  created_at: number;
  updated_at: number;
  submitted_students?: number;
}

export type ScoreMode = "best" | "first";

export interface PracticeSetDraft extends PracticeSetSummary {
  questions: PracticeQuestion[];
}

export interface AssignedSet extends PracticeSetSummary {
  teacher: string;
  /** Where it comes from (spec 2026-11-02 §3.1). */
  provenance?: Provenance;
  submissions: number;
  best_score: number | null;
  max_score: number | null;
  /** Polish batch §1: taken down, or archived by its author; one's results stay, read-only. */
  unavailable?: "taken_down" | "archived" | null;
  /** Taken out of the market by its author: still answered. */
  unlisted?: boolean;
}

/** ``pending``: graded, but withheld until the answers may show. */
export type ResultValue = "correct" | "partial" | "incorrect" | "ungraded" | "pending";

export interface QuestionResult {
  question_id: string;
  result: ResultValue;
  /** Null while the result is withheld. */
  points: number | null;
  max_points: number;
  /** What the learner answered. */
  answer?: string;
  feedback?: string;
  correct_answer?: string;
  explanation?: string;
}

export interface TakeView extends PracticeSetSummary {
  teacher: string;
  /** ``null`` when the set has no attempt limit. */
  attempts_left?: number | null;
  /** R6 / batch 4 §1: on the current version's full marks; ``null`` with fewer than 3 scores. */
  class_average?: number | null;
  answers_revealed: boolean;
  questions: PracticeQuestion[];
  /** ``score`` is null while results are withheld. */
  submissions: { id: string; revision: number; submitted_at: number; score: number | null; max_score: number }[];
  /** Version of the latest attempt, whose results ``latest_results`` are. */
  latest_revision: number | null;
  latest_results: QuestionResult[];
  /** Polish batch §1: gone; the view is one's last attempt, read-only. */
  unavailable?: "taken_down" | "archived" | null;
}

export interface SubmitResult {
  submission_id: string;
  revision?: number;
  /** Null while results are withheld. */
  score: number | null;
  max_score: number;
  answers_revealed: boolean;
  results: QuestionResult[];
}

export interface PracticeReport {
  set_id: string;
  title: string;
  students: {
    id: string;
    username: string;
    completed: boolean;
    attempts: number;
    first_score: number | null;
    best_score: number | null;
    /** The maximum of the version the best score was on. */
    best_max_score: number | null;
    /** The latest attempt, for reviewing its answers. */
    latest_submission_id?: string | null;
    max_score: number | null;
    minutes: number | null;
    submitted_at: number | null;
    in_scope?: boolean;
    /** The attempt that counts under the set's score mode. */
    score?: number | null;
    score_max?: number | null;
    /** Answers the AI could not judge, waiting for the author. */
    ungraded?: number;
  }[];
  score_mode?: ScoreMode;
  attempts_limit?: number | null;
  questions: {
    question_id: string;
    question: string;
    answered: number;
    error_rate: number | null;
    question_type?: PracticeQuestionType;
    options?: Record<string, string>;
    correct_answer?: string;
    /** Picks per option (``TRUE``/``FALSE`` for true-or-false); ``null`` without options. */
    option_counts?: Record<string, number> | null;
  }[];
}

/** One attempt, question by question, for the set's author (batch 3 §6.1). */
export interface ReviewAttempt {
  id: string;
  set_id: string;
  revision: number;
  student_id: string;
  username: string;
  submitted_at: number;
  score: number;
  max_score: number;
  questions: {
    question_id: string;
    question: string;
    question_type: PracticeQuestionType;
    options: Record<string, string>;
    correct_answer: string;
    result: ResultValue;
    points: number;
    max_points: number;
    /** Subjective answers' text, or the option picked; ``null`` when not kept. */
    answer: string | null;
  }[];
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
  update: Partial<Pick<PracticeSetDraft, "title" | "description" | "questions" | "reveal" | "score_mode">> & {
    attempts_limit?: number | null;
  },
) => {
  const { attempts_limit, ...rest } = update;
  const body: Record<string, unknown> = { ...rest };
  if (attempts_limit === null) body.clear_attempts_limit = true;
  else if (attempts_limit !== undefined) body.attempts_limit = attempts_limit;
  return call<{ set: PracticeSetDraft }>(id(setId), send("PUT", body)).then((r) => r.set);
};
export const publishSet = (setId: string) =>
  call<{ set: PracticeSetDraft }>(`${id(setId)}/publish`, send("POST")).then((r) => r.set);
export const archiveSet = (setId: string) =>
  call<{ set: PracticeSetDraft }>(`${id(setId)}/archive`, send("POST")).then((r) => r.set);
/** Administrators: other people's published sets, which they may only withdraw (D10). */
export const listWithdrawable = () =>
  call<{ sets: (PracticeSetSummary & { owner: string })[] }>("/withdrawable").then((r) => r.sets);
export const deleteSet = (setId: string) => call<{ ok: boolean }>(id(setId), send("DELETE"));
export const generateQuestions = (body: {
  topic: string;
  count: number;
  kb_ref?: string | null;
  question_types?: string[];
  language?: string;
  /** Batch 3 §4: from a book (``book:<id>``), whole or some chapters. */
  book_ref?: string | null;
  chapter_ids?: string[];
}) => call<{ questions: PracticeQuestion[] }>("/generate", send("POST", body)).then((r) => r.questions);
export const questionsFromBank = (entryIds: number[]) =>
  call<{ questions: PracticeQuestion[] }>("/from-bank", send("POST", { entry_ids: entryIds })).then((r) => r.questions);
export const getReport = (setId: string) => call<PracticeReport>(`${id(setId)}/report`);
/** The results as a CSV download (batch 3 §6.1). */
export const reportExportUrl = (setId: string) => apiUrl(`${BASE}${id(setId)}/export`);
/** Follow-ups §7: downloaded with the sign-in credentials. */
export const downloadSetReport = (setId: string) => downloadAuthenticated(reportExportUrl(setId), "practice-set-results.csv");
export const getAttempt = (setId: string, submissionId: string) =>
  call<{ attempt: ReviewAttempt }>(`${id(setId)}/submissions${id(submissionId)}`).then((r) => r.attempt);
export const listAssigned = () => call<{ sets: AssignedSet[] }>("/assigned").then((r) => r.sets);
export const takeSet = (setId: string) => call<{ set: TakeView }>(`${id(setId)}/take`).then((r) => r.set);
/** ``revision`` is the version the learner opened: answers are graded against it. */
export const submitSet = (
  setId: string,
  answers: Record<string, string>,
  startedAt: number | null,
  revision: number,
) => call<SubmitResult>(`${id(setId)}/submit`, send("POST", { answers, started_at: startedAt, revision }));
