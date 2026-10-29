"use client";

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { getClassReport, getStudentReport, type ClassReport, type StudentReport } from "@/lib/classes-api";
import { actionClass } from "./ClassesHub";

const cell = "px-2 py-1.5 text-left text-[12.5px]";

function when(timestamp: number | null | undefined, never: string): string {
  return timestamp ? new Date(timestamp * 1000).toLocaleDateString() : never;
}

/**
 * Learning report for a class (spec 2026-10-29 D17, option B): progress on this
 * teacher's paths plus an overall summary. Numbers only; no conversation text.
 */
export function ClassReportSection({ classId }: { classId: string }) {
  const { t } = useTranslation();
  const [report, setReport] = useState<ClassReport | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    getClassReport(classId)
      .then((next) => alive && setReport(next))
      .catch((err) => alive && setError(err instanceof Error ? t(err.message) : String(err)));
    return () => {
      alive = false;
    };
  }, [classId, open, t]);

  if (!open) {
    return (
      <button type="button" className={actionClass} onClick={() => setOpen(true)}>
        {t("View learning report")}
      </button>
    );
  }
  if (error) return <p role="alert" className="text-[12px] text-[var(--destructive)]">{error}</p>;
  if (!report) return <div aria-busy="true" className="h-24 animate-pulse rounded-xl bg-[var(--muted)]" />;
  if (report.students.length === 0) {
    return <p className="text-[12px] text-[var(--muted-foreground)]">{t("No students yet.")}</p>;
  }
  return (
    <div className="space-y-3">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px]">
          <thead className="text-[11.5px] text-[var(--muted-foreground)]">
            <tr>
              <th className={cell}>{t("Student")}</th>
              <th className={cell}>{t("My paths: mastered points")}</th>
              <th className={cell}>{t("My paths: wrong answers")}</th>
              <th className={cell}>{t("All learning: mastered points")}</th>
              <th className={cell}>{t("Question bank: wrong")}</th>
              <th className={cell}>{t("Study time (7 days / total)")}</th>
              <th className={cell}>{t("Last activity")}</th>
            </tr>
          </thead>
          <tbody>
            {report.students.map((student) => {
              const mine = student.teacher_paths.reduce(
                (acc, path) => ({
                  mastered: acc.mastered + path.kp_mastered,
                  total: acc.total + path.kp_total,
                  wrong: acc.wrong + path.attempts.incorrect,
                }),
                { mastered: 0, total: 0, wrong: 0 },
              );
              return (
                <tr
                  key={student.id}
                  onClick={() => setSelected(selected === student.id ? null : student.id)}
                  className={`cursor-pointer border-t border-[var(--border)] hover:bg-[var(--muted)]/40 ${
                    selected === student.id ? "bg-[var(--muted)]/40" : ""
                  }`}
                >
                  <td className={`${cell} font-medium text-[var(--foreground)]`}>{student.username}</td>
                  <td className={cell}>{mine.total ? `${mine.mastered} / ${mine.total}` : "—"}</td>
                  <td className={cell}>{mine.total ? mine.wrong : "—"}</td>
                  <td className={cell}>
                    {student.overall.kp_mastered} / {student.overall.kp_total}
                  </td>
                  <td className={cell}>{student.overall.question_bank.incorrect}</td>
                  <td className={cell}>
                    {t("{{week}} / {{total}} min", {
                      week: student.overall.study_minutes_7d,
                      total: student.overall.study_minutes_total,
                    })}
                  </td>
                  <td className={cell}>{when(student.overall.last_activity, t("Never"))}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-[11.5px] text-[var(--muted-foreground)]">
        {t("Reports show progress numbers only. Students' conversations and notes stay private.")}
      </p>
      {selected && <StudentDetail classId={classId} studentId={selected} />}
    </div>
  );
}

function StudentDetail({ classId, studentId }: { classId: string; studentId: string }) {
  const { t } = useTranslation();
  const [report, setReport] = useState<StudentReport | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    setReport(null);
    getStudentReport(classId, studentId)
      .then((next) => alive && setReport(next))
      .catch((err) => alive && setError(err instanceof Error ? t(err.message) : String(err)));
    return () => {
      alive = false;
    };
  }, [classId, studentId, t]);

  if (error) return <p role="alert" className="text-[12px] text-[var(--destructive)]">{error}</p>;
  if (!report) return <div aria-busy="true" className="h-20 animate-pulse rounded-xl bg-[var(--muted)]" />;
  return (
    <div className="space-y-3 rounded-xl border border-[var(--border)] p-3">
      <h4 className="text-[13px] font-medium text-[var(--foreground)]">{report.username}</h4>
      {report.teacher_paths.length === 0 ? (
        <p className="text-[12px] text-[var(--muted-foreground)]">{t("No paths from you yet.")}</p>
      ) : (
        report.teacher_paths.map((path) => (
          <div key={path.template_id} className="space-y-1.5">
            <p className="text-[12.5px] font-medium text-[var(--foreground)]">
              {path.title}
              <span className="ml-2 text-[11.5px] font-normal text-[var(--muted-foreground)]">
                {t("{{correct}} correct · {{partial}} partial · {{incorrect}} wrong", { ...path.attempts })}
              </span>
            </p>
            <ul className="grid gap-1 sm:grid-cols-2">
              {(path.knowledge_points ?? []).map((kp, index) => (
                <li key={index} className="flex items-center gap-2 text-[12px]">
                  <span className="min-w-0 flex-1 truncate text-[var(--foreground)]">{kp.name}</span>
                  <span className="h-1.5 w-20 overflow-hidden rounded-full bg-[var(--muted)]">
                    <span
                      className={`block h-full ${kp.mastered ? "bg-emerald-500" : "bg-[var(--primary)]"}`}
                      style={{ width: `${Math.round(kp.mastery * 100)}%` }}
                    />
                  </span>
                  <span className="w-9 text-right tabular-nums text-[var(--muted-foreground)]">
                    {Math.round(kp.mastery * 100)}%
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ))
      )}
    </div>
  );
}
