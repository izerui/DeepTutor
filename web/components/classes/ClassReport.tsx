"use client";

import { Sparkles } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  classReportExportUrl,
  getClassPathPoints,
  getClassReport,
  getStudentReport,
  type ClassPathPoints,
  type ClassReport,
  type StudentReport,
} from "@/lib/classes-api";
import { remedialFromPath } from "@/lib/practice-sets-api";
import { CommentEditor } from "@/components/comments/Comments";
import { rememberSuggested } from "@/components/practice-sets/SetReview";
import { flagText } from "./attention-format";
import { actionClass, primaryClass } from "./ClassesHub";

const cell = "px-2 py-1.5 text-left text-[12.5px]";
const errorText = (err: unknown, t: (key: string) => string) => (err instanceof Error ? t(err.message) : String(err));

function when(timestamp: number | null | undefined, never: string): string {
  return timestamp ? new Date(timestamp * 1000).toLocaleDateString() : never;
}

/** Eight weeks of study minutes as a small line (spec 2026-11-03 batch 3 §6.3). */
function Trend({ weeks }: { weeks: { minutes: number; answers: number }[] }) {
  const { t } = useTranslation();
  if (!weeks.length) return null;
  const top = Math.max(1, ...weeks.map((w) => w.minutes));
  const points = weeks.map((w, i) => `${(i * 60) / (weeks.length - 1 || 1)},${18 - (16 * w.minutes) / top}`).join(" ");
  return (
    <svg width="64" height="20" viewBox="0 0 64 20" role="img"
      aria-label={t("Study minutes over 8 weeks: {{values}}", { values: weeks.map((w) => w.minutes).join(", ") })}>
      <polyline points={points} fill="none" stroke="currentColor" strokeWidth="1.5" className="text-[var(--primary)]" />
    </svg>
  );
}

/** Turns a remedial draft into the practice-set editor, its suggested receivers handed over. */
function useOpenRemedial() {
  const router = useRouter();
  return (draft: Awaited<ReturnType<typeof remedialFromPath>>) => {
    rememberSuggested(draft);
    router.push(`/practice-sets?set=${encodeURIComponent(draft.set.id)}`);
  };
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
  const [days, setDays] = useState<number | null>(7);
  const [onlyFlagged, setOnlyFlagged] = useState(false);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    getClassReport(classId, days)
      .then((next) => alive && setReport(next))
      .catch((err) => alive && setError(errorText(err, t)));
    return () => {
      alive = false;
    };
  }, [classId, open, t, days]);

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
  const rows = onlyFlagged ? report.students.filter((s) => (s.flags ?? []).length > 0) : report.students;
  const paths = new Map<string, string>();
  for (const student of report.students) for (const path of student.teacher_paths) paths.set(path.template_id, path.title);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3 text-[12.5px]">
        <label className="flex items-center gap-1.5">
          {t("Period")}
          <select aria-label={t("Period")} className="h-8 rounded-lg border border-[var(--border)] bg-[var(--background)] px-2"
            value={days ?? ""} onChange={(e) => setDays(e.target.value ? Number(e.target.value) : null)}>
            <option value="7">{t("Last 7 days")}</option>
            <option value="30">{t("Last 30 days")}</option>
            <option value="">{t("All time")}</option>
          </select>
        </label>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={onlyFlagged} onChange={(e) => setOnlyFlagged(e.target.checked)} />
          {t("Only students who need attention")}
        </label>
        <a className={actionClass} href={classReportExportUrl(classId, days)} download>{t("Export CSV")}</a>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px]">
          <thead className="text-[11.5px] text-[var(--muted-foreground)]">
            <tr>
              <th className={cell}>{t("Student")}</th>
              <th className={cell}>{t("Needs attention")}</th>
              <th className={cell}>{t("My paths: mastered points")}</th>
              <th className={cell}>{t("My practice sets: submitted")}</th>
              <th className={cell}>{t("All learning: mastered points")}</th>
              <th className={cell}>{t("Study time in period")}</th>
              <th className={cell}>{t("Answers in period")}</th>
              <th className={cell}>{t("8 weeks")}</th>
              <th className={cell}>{t("Last activity")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((student) => {
              const mine = student.teacher_paths.reduce(
                (acc, path) => ({ mastered: acc.mastered + path.kp_mastered, total: acc.total + path.kp_total }),
                { mastered: 0, total: 0 },
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
                  <td className={cell}>
                    <ul className="space-y-0.5">
                      {(student.flags ?? []).map((flag, index) => (
                        <li key={index} className="text-[11.5px] text-amber-700 dark:text-amber-300">{flagText(flag, t)}</li>
                      ))}
                    </ul>
                  </td>
                  <td className={cell}>{mine.total ? `${mine.mastered} / ${mine.total}` : "—"}</td>
                  <td className={cell}>{(student.practice_sets ?? []).length || "—"}</td>
                  <td className={cell}>{student.overall.kp_mastered} / {student.overall.kp_total}</td>
                  <td className={cell}>{t("{{minutes}} min", { minutes: student.overall.period_minutes ?? 0 })}</td>
                  <td className={cell}>{student.overall.period_answers ?? 0}</td>
                  <td className={cell}><Trend weeks={student.overall.trend ?? []} /></td>
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
      {paths.size > 0 && <ClassPathView classId={classId} paths={paths} />}
    </div>
  );
}

/** One of my paths across the class: who has mastered each point; remedial for the rest (§3.1). */
function ClassPathView({ classId, paths }: { classId: string; paths: Map<string, string> }) {
  const { t } = useTranslation();
  const openRemedial = useOpenRemedial();
  const [templateId, setTemplateId] = useState("");
  const [data, setData] = useState<ClassPathPoints | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!templateId) return;
    let alive = true;
    getClassPathPoints(classId, templateId)
      .then((next) => {
        if (!alive) return;
        setData(next);
        // Points fewer than 60% of the class have mastered start picked.
        setPicked(next.points.filter((p) => p.students && p.mastered / p.students < 0.6).map((p) => p.id));
      })
      .catch((err) => alive && setError(errorText(err, t)));
    return () => {
      alive = false;
    };
  }, [classId, templateId, t]);

  const students = new Set((data?.points ?? []).filter((p) => picked.includes(p.id)).flatMap((p) => p.not_mastered));
  return (
    <div className="space-y-2 rounded-xl border border-[var(--border)] p-3" data-testid="class-path-view">
      <label className="flex items-center gap-2 text-[12.5px]">
        {t("Knowledge points of my path")}
        <select className="h-8 rounded-lg border border-[var(--border)] bg-[var(--background)] px-2" value={templateId}
          onChange={(e) => { setData(null); setTemplateId(e.target.value); }}>
          <option value="">{t("Choose a path")}</option>
          {[...paths].map(([id, title]) => <option key={id} value={id}>{title}</option>)}
        </select>
      </label>
      {data && (
        <>
          <ul className="space-y-1 text-[12px]">
            {data.points.map((point) => (
              <li key={point.id} className="flex items-center gap-2">
                <input type="checkbox" aria-label={t("Pick {{name}}", { name: point.name })} checked={picked.includes(point.id)}
                  onChange={() => setPicked(picked.includes(point.id) ? picked.filter((p) => p !== point.id) : [...picked, point.id])} />
                <span className="min-w-0 flex-1 truncate">{point.name}</span>
                <span className="tabular-nums text-[var(--muted-foreground)]">
                  {t("{{mastered}} / {{total}} mastered", { mastered: point.mastered, total: point.students })}
                </span>
              </li>
            ))}
          </ul>
          <button type="button" className={primaryClass} disabled={busy || picked.length === 0}
            onClick={() => {
              setBusy(true);
              setError("");
              remedialFromPath(data.template_id, picked, [...students], t("Remedial: "))
                .then(openRemedial)
                .catch((err) => setError(errorText(err, t)))
                .finally(() => setBusy(false));
            }}>
            <Sparkles size={13} />
            {busy ? t("Generating…") : t("Make remedial practice for {{count}} students", { count: students.size })}
          </button>
        </>
      )}
      {error && <p role="alert" className="text-[12px] text-red-600">{error}</p>}
    </div>
  );
}

function StudentDetail({ classId, studentId }: { classId: string; studentId: string }) {
  const { t } = useTranslation();
  const openRemedial = useOpenRemedial();
  const [report, setReport] = useState<StudentReport | null>(null);
  const [error, setError] = useState("");
  const [picked, setPicked] = useState<Record<string, string[]>>({});
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    setReport(null);
    getStudentReport(classId, studentId)
      .then((next) => alive && setReport(next))
      .catch((err) => alive && setError(errorText(err, t)));
    return () => {
      alive = false;
    };
  }, [classId, studentId, t]);

  if (error) return <p role="alert" className="text-[12px] text-[var(--destructive)]">{error}</p>;
  if (!report) return <div aria-busy="true" className="h-20 animate-pulse rounded-xl bg-[var(--muted)]" />;
  return (
    <div className="space-y-3 rounded-xl border border-[var(--border)] p-3">
      <h4 className="text-[13px] font-medium text-[var(--foreground)]">{report.username}</h4>
      {(report.practice_sets ?? []).length > 0 && (
        <ul className="space-y-1 text-[12.5px]">
          {(report.practice_sets ?? []).map((set) => (
            <li key={set.set_id} className="flex flex-wrap gap-x-3">
              <span className="font-medium text-[var(--foreground)]">{set.title}</span>
              <span className="text-[var(--muted-foreground)]">
                {t("First {{first}} / {{max}} · best {{best}} / {{bestMax}} · {{count}} attempts", {
                  first: set.first_score, max: set.max_score, best: set.best_score,
                  bestMax: set.best_max_score, count: set.attempts,
                })}
                {set.late && ` · ${t("Late")}`}
              </span>
            </li>
          ))}
        </ul>
      )}
      {report.teacher_paths.length === 0 ? (
        <p className="text-[12px] text-[var(--muted-foreground)]">{t("No paths from you yet.")}</p>
      ) : (
        report.teacher_paths.map((path) => {
          const chosen = picked[path.template_id] ?? [];
          return (
            <div key={path.template_id} className="space-y-1.5">
              <p className="text-[12.5px] font-medium text-[var(--foreground)]">
                {path.title}
                <span className="ml-2 text-[11.5px] font-normal text-[var(--muted-foreground)]">
                  {t("{{correct}} correct · {{partial}} partial · {{incorrect}} wrong", { ...path.attempts })}
                </span>
              </p>
              <ul className="grid gap-1 sm:grid-cols-2">
                {(path.knowledge_points ?? []).map((kp, index) => (
                  <li key={index} className="space-y-0.5 text-[12px]">
                    <div className="flex items-center gap-2">
                      {!kp.mastered && (
                        <input type="checkbox" aria-label={t("Pick {{name}}", { name: kp.name })} checked={chosen.includes(kp.id)}
                          onChange={() => setPicked({
                            ...picked,
                            [path.template_id]: chosen.includes(kp.id) ? chosen.filter((k) => k !== kp.id) : [...chosen, kp.id],
                          })} />
                      )}
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
                    </div>
                    {(kp.answers ?? 0) > 0 && (
                      <p className="text-[11px] text-[var(--muted-foreground)]">
                        {t("{{answers}} answers, {{wrong}} wrong", { answers: kp.answers, wrong: kp.wrong ?? 0 })}
                        {kp.stuck_days != null && ` · ${t("stuck for {{days}} days", { days: kp.stuck_days })}`}
                      </p>
                    )}
                    {kp.id && <CommentEditor kind="path_point" subject={path.template_id} target={kp.id} studentId={studentId} />}
                  </li>
                ))}
              </ul>
              {chosen.length > 0 && (
                <button type="button" className={primaryClass} disabled={busy}
                  onClick={() => {
                    setBusy(true);
                    setError("");
                    remedialFromPath(path.template_id, chosen, [studentId], t("Remedial: "))
                      .then(openRemedial)
                      .catch((err) => setError(errorText(err, t)))
                      .finally(() => setBusy(false));
                  }}>
                  <Sparkles size={13} />
                  {busy ? t("Generating…") : t("Make remedial practice")}
                </button>
              )}
            </div>
          );
        })
      )}
    </div>
  );
}
