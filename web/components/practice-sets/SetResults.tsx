"use client";

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { actionClass } from "@/components/common/form-styles";
import { DownloadButton } from "@/components/common/DownloadButton";
import { downloadSetReport, getReport, type PracticeReport } from "@/lib/practice-sets-api";
import { formatDate } from "./practice-format";
import { AttemptReview, OptionBars } from "./SetReview";

const sectionClass = "space-y-3 rounded-xl border border-[var(--border)] p-4";
const titleClass = "text-[13.5px] font-medium text-[var(--foreground)]";
const hintClass = "text-[12px] leading-relaxed text-[var(--muted-foreground)]";

/**
 * A practice set's results: each learner's scores, their answers, the error
 * rate per question. For its author in the editor and, read only, for
 * administrators from a learning report (spec 2026-11-05 one-learner-role §2).
 */
export function SetResults({ setId, initiallyOpen = false }: { setId: string; initiallyOpen?: boolean }) {
  const { t } = useTranslation();
  const [report, setReport] = useState<PracticeReport | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(initiallyOpen);
  const [reviewing, setReviewing] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    getReport(setId)
      .then((next) => alive && setReport(next))
      .catch((err) => alive && setError(err instanceof Error ? t(err.message) : String(err)));
    return () => {
      alive = false;
    };
  }, [open, setId, t]);

  return (
    <section className={sectionClass}>
      <div className="flex items-center justify-between gap-2">
        <h3 className={titleClass}>{t("Results")}</h3>
        {open && (
          <DownloadButton download={() => downloadSetReport(setId)} label={t("Export CSV")} />
        )}
      </div>
      {!open ? (
        <button type="button" className={actionClass} onClick={() => setOpen(true)}>
          {t("View results")}
        </button>
      ) : error ? (
        <p role="alert" className="text-[12px] text-[var(--destructive)]">{error}</p>
      ) : !report ? (
        <div aria-busy="true" className="h-20 animate-pulse rounded-xl bg-[var(--muted)]" />
      ) : report.students.length === 0 ? (
        <p className={hintClass}>{t("Nobody receives this practice set yet.")}</p>
      ) : (
        <div className="space-y-4">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] text-[12.5px]">
              <thead className="text-left text-[11.5px] text-[var(--muted-foreground)]">
                <tr>
                  <th className="px-2 py-1.5">{t("Learner")}</th>
                  <th className="px-2 py-1.5">{t("First score")}</th>
                  <th className="px-2 py-1.5">{t("Best score")}</th>
                  <th className="px-2 py-1.5">{t("Attempts")}</th>
                  <th className="px-2 py-1.5">{t("Minutes")}</th>
                  <th className="px-2 py-1.5">{t("Submitted")}</th>
                  <th className="px-2 py-1.5">{t("Review")}</th>
                </tr>
              </thead>
              <tbody>
                {report.students.map((student) => (
                  <tr key={student.id} className="border-t border-[var(--border)]">
                    <td className="px-2 py-1.5 font-medium">{student.username}</td>
                    <td className="px-2 py-1.5">{student.completed ? `${student.first_score} / ${student.max_score}` : t("Not submitted")}</td>
                    <td className="px-2 py-1.5">{student.completed ? `${student.best_score} / ${student.best_max_score}` : "—"}</td>
                    <td className="px-2 py-1.5">{student.attempts}</td>
                    <td className="px-2 py-1.5">{student.minutes ?? "—"}</td>
                    <td className="px-2 py-1.5">
                      {formatDate(student.submitted_at) || "—"}
                    </td>
                    <td className="px-2 py-1.5">
                      {student.latest_submission_id && (
                        <button type="button" className="text-[11.5px] text-[var(--primary)] hover:underline"
                          onClick={() => setReviewing(student.latest_submission_id ?? null)}>
                          {t("Review answers")}
                        </button>
                      )}
                      {(student.ungraded ?? 0) > 0 && (
                        <span className="ml-1 rounded-full bg-amber-100 px-1.5 text-[11px] text-amber-800 dark:bg-amber-900/40 dark:text-amber-200">
                          {t("{{count}} to grade", { count: student.ungraded })}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {reviewing && (
            <AttemptReview key={reviewing} setId={setId} submissionId={reviewing}
              onClose={() => setReviewing(null)} />
          )}
          <div className="space-y-2">
            <h4 className="text-[12.5px] font-medium">{t("Error rate per question")}</h4>
            <ol className="space-y-2 text-[12.5px]">
              {report.questions.map((question, index) => (
                <li key={question.question_id}>
                  <div className="flex gap-2">
                    <span className="w-12 shrink-0 tabular-nums">
                      {question.error_rate === null ? "—" : `${Math.round(question.error_rate * 100)}%`}
                    </span>
                    <span className="min-w-0 truncate">
                      {index + 1}. {question.question}
                    </span>
                  </div>
                  <OptionBars question={question} />
                </li>
              ))}
            </ol>
          </div>
          <p className={hintClass}>{t("Answers to short-answer questions and the options picked are shown only to the author and administrators.")}</p>
        </div>
      )}
    </section>
  );
}
