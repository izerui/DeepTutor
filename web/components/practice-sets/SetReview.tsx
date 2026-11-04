"use client";

import { Sparkles, X } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { primaryClass } from "@/components/common/form-styles";
import { bookApi } from "@/lib/book-api";
import { browserStorage } from "@/shared/storage";
import type { Book, Chapter } from "@/lib/book-types";
import {
  getAttempt,
  regradeQuestion,
  remedialFromSet,
  type PracticeReport,
  type RemedialDraft,
  type ReviewAttempt,
} from "@/lib/practice-sets-api";

const errorText = (err: unknown, t: (key: string) => string) => (err instanceof Error ? t(err.message) : String(err));

const RESULT_LABEL: Record<string, string> = {
  correct: "Correct",
  partial: "Partly correct",
  incorrect: "Wrong",
  ungraded: "Not graded",
  pending: "Pending",
};

/** Where remedial drafts hand their suggested receivers to the editor that opens next. */
export const SUGGESTED_KEY = (setId: string) => `practice-remedial-suggest:${setId}`;

export function rememberSuggested(draft: RemedialDraft) {
  // Unavailable storage just means the teacher picks receivers by hand.
  browserStorage.writeRaw("session", SUGGESTED_KEY(draft.set.id), JSON.stringify(draft.suggested_students));
}

export function takeSuggested(setId: string): { id: string; username: string }[] {
  const raw = browserStorage.readRaw("session", SUGGESTED_KEY(setId));
  try {
    const value: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(value) ? (value as { id: string; username: string }[]) : [];
  } catch {
    return [];
  }
}

/** How many picked each option, the right one marked (spec 2026-11-03 batch 3 §6.1). */
export function OptionBars({ question }: { question: PracticeReport["questions"][number] }) {
  const { t } = useTranslation();
  const counts = question.option_counts;
  if (!counts) return null;
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const right = new Set(
    question.question_type === "concept"
      ? [/^(t|true|对|正确|是|yes)$/i.test(question.correct_answer ?? "") ? "TRUE" : "FALSE"]
      : (question.correct_answer ?? "").toUpperCase().split(/[^A-Z]+/).filter(Boolean),
  );
  return (
    <ul className="ml-14 mt-1 space-y-0.5" aria-label={t("Option distribution")}>
      {Object.entries(counts).map(([key, n]) => {
        const label = key === "TRUE" ? t("True") : key === "FALSE" ? t("False") : `${key}. ${question.options?.[key] ?? ""}`;
        const share = total ? Math.round((100 * n) / total) : 0;
        return (
          <li key={key} className="flex items-center gap-2 text-[11.5px]">
            <span className={`w-40 truncate ${right.has(key) ? "font-semibold text-emerald-700 dark:text-emerald-400" : ""}`}>
              {label}
            </span>
            <span className="h-1.5 w-28 overflow-hidden rounded-full bg-[var(--muted)]">
              <span className={`block h-full ${right.has(key) ? "bg-emerald-500" : "bg-[var(--primary)]"}`} style={{ width: `${share}%` }} />
            </span>
            <span className="tabular-nums text-[var(--muted-foreground)]">{n} · {share}%</span>
          </li>
        );
      })}
    </ul>
  );
}

/** One attempt question by question; the author changes a question's points (§6.1). */
export function AttemptReview({
  setId,
  submissionId,
  onClose,
  onChanged,
}: {
  setId: string;
  submissionId: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const [attempt, setAttempt] = useState<ReviewAttempt | null>(null);
  const [points, setPoints] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  // A failed change is shown beside the attempt, which stays on screen.
  const [saveError, setSaveError] = useState("");

  useEffect(() => {
    let alive = true;
    getAttempt(setId, submissionId)
      .then((next) => alive && setAttempt(next))
      .catch((err) => alive && setError(errorText(err, t)));
    return () => {
      alive = false;
    };
  }, [setId, submissionId, t]);

  if (error) return <p role="alert" className="text-[12px] text-red-600">{error}</p>;
  if (!attempt) return <div aria-busy="true" className="h-20 animate-pulse rounded-xl bg-[var(--muted)]" />;
  return (
    <div className="space-y-2 rounded-xl border border-[var(--border)] p-3" data-testid="attempt-review">
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-[13px] font-medium">
          {t("{{name}}'s attempt", { name: attempt.username })} · {t("Score {{score}} / {{max}}", { score: attempt.score, max: attempt.max_score })}
        </h4>
        <button type="button" aria-label={t("Close")} onClick={onClose}><X size={14} /></button>
      </div>
      <ol className="space-y-2">
        {attempt.questions.map((q, index) => (
          <li key={q.question_id} className="space-y-1 rounded-lg bg-[var(--muted)]/40 p-2 text-[12.5px]">
            <p className="whitespace-pre-line"><span className="font-semibold">{index + 1}.</span> {q.question}</p>
            {q.answer != null && (
              <p className="whitespace-pre-line rounded bg-[var(--background)] p-1.5">
                <span className="text-[var(--muted-foreground)]">{t("Answer")}: </span>{q.answer}
              </p>
            )}
            {q.correct_answer && (
              <p className="text-[11.5px] text-[var(--muted-foreground)]">{t("Reference answer")}: {q.correct_answer}</p>
            )}
            <div className="flex flex-wrap items-center gap-2 text-[11.5px]">
              <span className={q.result === "ungraded" ? "font-medium text-amber-700" : ""}>{t(RESULT_LABEL[q.result] ?? q.result)}</span>
              {q.regraded && <span className="rounded-full bg-[var(--muted)] px-1.5">{t("Changed by you")}</span>}
              <label className="flex items-center gap-1">
                {t("Points")}
                <input type="number" min={0} max={q.max_points} step={0.5} aria-label={t("Points for question {{n}}", { n: index + 1 })}
                  className="h-7 w-16 rounded border border-[var(--border)] bg-[var(--background)] px-1"
                  value={points[q.question_id] ?? String(q.points)}
                  onChange={(e) => setPoints({ ...points, [q.question_id]: e.target.value })} />
                / {q.max_points}
              </label>
              {points[q.question_id] !== undefined && Number(points[q.question_id]) !== q.points && (
                <button type="button" className="rounded bg-[var(--primary)] px-2 py-0.5 text-[var(--primary-foreground)]"
                  onClick={() => void regradeQuestion(setId, submissionId, q.question_id, Number(points[q.question_id]))
                    .then((next) => { setAttempt(next); setPoints({}); setSaveError(""); onChanged(); })
                    .catch((err) => setSaveError(errorText(err, t)))}>
                  {t("Save points")}
                </button>
              )}
            </div>
          </li>
        ))}
      </ol>
      {saveError && <p role="alert" className="text-[12px] text-red-600">{saveError}</p>}
      <p className="text-[11px] text-[var(--muted-foreground)]">{t("The student is told when you change a score.")}</p>
    </div>
  );
}

/** Practise again what many got wrong (§3.1): the picked questions, plus AI look-alikes. */
export function RemedialFromSet({
  setId,
  questionIds,
  onCreated,
}: {
  setId: string;
  questionIds: string[];
  onCreated: (draft: RemedialDraft) => void;
}) {
  const { t } = useTranslation();
  const [similar, setSimilar] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg bg-[var(--muted)]/40 p-2 text-[12px]" data-testid="remedial-from-set">
      <span>{t("{{count}} questions picked", { count: questionIds.length })}</span>
      <label className="flex items-center gap-1">
        {t("Similar questions per question")}
        <select className="h-7 rounded border border-[var(--border)] bg-[var(--background)] px-1" value={similar}
          onChange={(e) => setSimilar(Number(e.target.value))}>
          {[0, 1, 2, 3].map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
      </label>
      <button type="button" className={primaryClass} disabled={busy || questionIds.length === 0}
        onClick={() => {
          setBusy(true);
          setError("");
          remedialFromSet(setId, questionIds, similar, t("Remedial: "))
            .then((draft) => { rememberSuggested(draft); onCreated(draft); })
            .catch((err) => setError(errorText(err, t)))
            .finally(() => setBusy(false));
        }}>
        <Sparkles size={13} />
        {busy ? t("Generating…") : t("Make remedial practice")}
      </button>
      {error && <span role="alert" className="text-red-600">{error}</span>}
    </div>
  );
}

/** A book and some of its chapters as the material for AI questions (§4). */
export function BookSource({
  value,
  onChange,
}: {
  value: { bookRef: string; chapterIds: string[] };
  onChange: (value: { bookRef: string; chapterIds: string[] }) => void;
}) {
  const { t } = useTranslation();
  const [books, setBooks] = useState<Book[]>([]);
  const [chapters, setChapters] = useState<Chapter[]>([]);
  useEffect(() => {
    bookApi.list().then((r) => setBooks(r.books)).catch(() => setBooks([]));
  }, []);
  useEffect(() => {
    if (!value.bookRef) return;
    bookApi.getSpine(value.bookRef).then((r) => setChapters(r.spine.chapters)).catch(() => setChapters([]));
  }, [value.bookRef]);
  return (
    <div className="space-y-1.5">
      <label className="flex items-center gap-1.5">
        {t("Book")}
        <select className="h-8 max-w-[240px] rounded-lg border border-[var(--border)] bg-[var(--background)] px-2"
          value={value.bookRef} onChange={(e) => { setChapters([]); onChange({ bookRef: e.target.value, chapterIds: [] }); }}>
          <option value="">{t("Choose a book")}</option>
          {books.map((book) => <option key={book.id} value={book.id}>{book.title}</option>)}
        </select>
      </label>
      {value.bookRef && chapters.length > 0 && (
        <fieldset className="flex flex-wrap gap-x-3 gap-y-1">
          <legend className="text-[11.5px] text-[var(--muted-foreground)]">{t("Chapters (none ticked: the whole book)")}</legend>
          {chapters.map((chapter) => (
            <label key={chapter.id} className="flex items-center gap-1">
              <input type="checkbox" checked={value.chapterIds.includes(chapter.id)}
                onChange={() => onChange({
                  ...value,
                  chapterIds: value.chapterIds.includes(chapter.id)
                    ? value.chapterIds.filter((c) => c !== chapter.id) : [...value.chapterIds, chapter.id],
                })} />
              {chapter.title}
            </label>
          ))}
        </fieldset>
      )}
    </div>
  );
}

