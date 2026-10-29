"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { primaryClass } from "@/components/classes/ClassesHub";
import { submitSet, takeSet, type ResultValue, type SubmitResult, type TakeView } from "@/lib/practice-sets-api";
import { formatDate } from "./PracticeSetsHub";
import { QuestionAnswer } from "./QuestionAnswer";

const RESULT_LABEL: Record<ResultValue, string> = {
  correct: "Correct",
  partial: "Partly correct",
  incorrect: "Incorrect",
  ungraded: "Not graded",
};
const RESULT_TONE: Record<ResultValue, string> = {
  correct: "text-emerald-600",
  partial: "text-amber-600",
  incorrect: "text-red-600",
  ungraded: "text-[var(--muted-foreground)]",
};

/** A student answers a practice set; every submission is graded and kept (spec §8.3). */
export function TakeSet({ setId }: { setId: string }) {
  const { t } = useTranslation();
  const [view, setView] = useState<TakeView | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [result, setResult] = useState<SubmitResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const startedAt = useRef<number>(Date.now() / 1000);

  useEffect(() => {
    let alive = true;
    takeSet(setId)
      .then((next) => alive && setView(next))
      .catch((err) => alive && setError(err instanceof Error ? t(err.message) : String(err)));
    return () => {
      alive = false;
    };
  }, [setId, t]);

  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      setResult(await submitSet(setId, answers, startedAt.current));
    } catch (err) {
      setError(err instanceof Error ? t(err.message) : String(err));
    } finally {
      setBusy(false);
    }
  };

  const again = () => {
    setResult(null);
    setAnswers({});
    startedAt.current = Date.now() / 1000;
  };

  if (error && !view) return <p role="alert" className="text-[12px] text-[var(--destructive)]">{error}</p>;
  if (!view) return <div aria-busy="true" className="h-40 animate-pulse rounded-xl bg-[var(--muted)]" />;
  const byQuestion = new Map((result?.results ?? []).map((row) => [row.question_id, row]));
  const unanswered = view.questions.filter((q) => !(answers[q.question_id] ?? "").trim()).length;

  return (
    <div className="space-y-4">
      <header className="space-y-1">
        <h2 className="text-[18px] font-semibold text-[var(--foreground)]">{view.title}</h2>
        <p className="text-[12.5px] text-[var(--muted-foreground)]">
          {view.teacher}
          {view.due_at ? ` · ${t("Due {{date}}", { date: formatDate(view.due_at) })}` : ""}
          {view.submissions.length > 0 &&
            ` · ${t("Submitted {{count}} times", { count: view.submissions.length })}`}
        </p>
        {view.description && <p className="whitespace-pre-line text-[13px]">{view.description}</p>}
      </header>

      {result && (
        <div role="status" className="rounded-xl border border-[var(--border)] bg-[var(--muted)]/40 p-4 text-[13px]">
          <p className="text-[15px] font-semibold">
            {t("Score {{score}} / {{max}}", { score: result.score, max: result.max_score })}
            {result.late && <span className="ml-2 text-[12px] text-amber-600">{t("Late")}</span>}
          </p>
          <p className="text-[12px] text-[var(--muted-foreground)]">
            {result.answers_revealed
              ? t("Wrong answers were added to your mistakes.")
              : t("Answers and explanations appear after the deadline.")}
          </p>
        </div>
      )}

      <ol className="space-y-4">
        {view.questions.map((question, index) => {
          const graded = byQuestion.get(question.question_id);
          return (
            <li key={question.question_id} className="space-y-2 rounded-xl border border-[var(--border)] p-4">
              <p className="whitespace-pre-line text-[13.5px] text-[var(--foreground)]">
                <span className="mr-1 font-semibold">{index + 1}.</span>
                {question.question}
                <span className="ml-2 text-[11.5px] text-[var(--muted-foreground)]">
                  {t("{{count}} points", { count: question.points })}
                </span>
              </p>
              <QuestionAnswer
                question={question}
                value={answers[question.question_id] ?? ""}
                disabled={busy || result !== null}
                onChange={(value) => setAnswers((current) => ({ ...current, [question.question_id]: value }))}
              />
              {graded && (
                <div className="space-y-1 text-[12.5px]">
                  <p className={RESULT_TONE[graded.result]}>
                    {t(RESULT_LABEL[graded.result])} · {graded.points} / {graded.max_points}
                  </p>
                  {graded.feedback && <p className="text-[var(--muted-foreground)]">{graded.feedback}</p>}
                  {graded.correct_answer !== undefined && (
                    <p>
                      <span className="font-medium">{t("Answer")}:</span> {graded.correct_answer}
                    </p>
                  )}
                  {graded.explanation && <p className="text-[var(--muted-foreground)]">{graded.explanation}</p>}
                </div>
              )}
            </li>
          );
        })}
      </ol>

      {error && <p role="alert" className="text-[12px] text-[var(--destructive)]">{error}</p>}
      {result ? (
        <button type="button" className={primaryClass} onClick={again}>
          {t("Answer again")}
        </button>
      ) : (
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            className={primaryClass}
            disabled={busy}
            onClick={() => {
              if (unanswered === 0 || window.confirm(t("{{count}} questions are unanswered. Submit anyway?", { count: unanswered }))) {
                void submit();
              }
            }}
          >
            {busy ? t("Grading…") : t("Submit")}
          </button>
        </div>
      )}
    </div>
  );
}
