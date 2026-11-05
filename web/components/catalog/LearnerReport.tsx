"use client";

import { ArrowLeft, Sparkles } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { actionClass, primaryClass } from "@/components/common/form-styles";
import { flagText } from "@/components/catalog/attention-format";
import { DownloadButton } from "@/components/common/DownloadButton";
import { PAGE_TITLE_CLASS } from "@/components/layout/FeaturePage";
import { rememberSuggested } from "@/components/practice-sets/SetReview";
import {
  getLearnerDetail,
  getLearnerReport,
  getReportPoints,
  downloadReport,
  REPORT_KINDS,
  type LearnerDetail,
  type LearnerReport as Report,
  type LearnerRow,
  type ReportKind,
  type ReportPoints,
} from "@/lib/catalog-api";
import { CommentEditor } from "@/components/comments/Comments";
import { writtenComments, type TeacherComment } from "@/lib/comments-api";
import { remedialFromPath } from "@/lib/practice-sets-api";

import { catalogError, itemHref, kindLabel } from "./catalog-format";

const cell = "px-2 py-1.5 text-left text-[12.5px]";
const selectClass = "h-8 rounded-lg border border-[var(--border)] bg-[var(--background)] px-2";

function when(seconds: number | null | undefined, never: string): string {
  return seconds ? new Date(seconds * 1000).toLocaleDateString() : never;
}

/** Where the author manages it: the course editor, or the path's intro page. */
const backHref = (kind: ReportKind, id: string) => (kind === "course" ? "/tasks?tab=courses" : itemHref(kind, id));

/**
 * A practice set opened from a report; from a course's report it carries the
 * course (follow-ups §3), so remedial practice made there can be added to it.
 */
export const practiceSetHref = (setId: string, kind: ReportKind, itemId: string) =>
  `/practice-sets?set=${encodeURIComponent(setId)}${kind === "course" ? `&course=${encodeURIComponent(itemId)}` : ""}`;

/** A remedial draft opens in the practice-set editor with its suggested receivers handed over. */
function useOpenRemedial(kind: ReportKind, itemId: string) {
  const router = useRouter();
  return (draft: Awaited<ReturnType<typeof remedialFromPath>>) => {
    rememberSuggested(draft);
    router.push(practiceSetHref(draft.set.id, kind, itemId));
  };
}

/** Mastered of total over the paths of this item. */
function mastered(row: Pick<LearnerRow, "paths">): { mastered: number; total: number } {
  return row.paths.reduce(
    (acc, path) => ({ mastered: acc.mastered + path.kp_mastered, total: acc.total + path.kp_total }),
    { mastered: 0, total: 0 },
  );
}

/** The best score over the practice sets of this course, as score of maximum. */
function practiceScore(row: Pick<LearnerRow, "practice_sets">): { score: number; max: number } | null {
  if (!row.practice_sets.length) return null;
  return row.practice_sets.reduce(
    (acc, set) => ({ score: acc.score + (set.best_score ?? 0), max: acc.max + (set.best_max_score ?? 0) }),
    { score: 0, max: 0 },
  );
}

/**
 * The learning report of a course or a Mastery Path (platform batch 3 §1):
 * ``/reports?kind=&id=``, for its author (a teacher). Numbers only.
 */
export default function LearnerReportPage() {
  const params = useSearchParams();
  const kind = params?.get("kind") ?? "";
  const id = params?.get("id") ?? "";
  const { t } = useTranslation();
  if (!REPORT_KINDS.includes(kind as ReportKind) || !id) {
    return <p className="text-[13px] text-[var(--muted-foreground)]">{t("Not found.")}</p>;
  }
  return <LearnerReportView key={`${kind}:${id}`} kind={kind as ReportKind} itemId={id} />;
}

export function LearnerReportView({ kind, itemId }: { kind: ReportKind; itemId: string }) {
  const { t } = useTranslation();
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState("");
  // Counted on this item only (a learner's study elsewhere stays theirs); 7 / 30 days or all time.
  const [days, setDays] = useState<number | null>(7);
  const [onlyFlagged, setOnlyFlagged] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    getLearnerReport(kind, itemId, days)
      .then((next) => {
        if (!alive) return;
        setReport(next);
        setError("");
      })
      .catch((err) => alive && setError(catalogError(err, t)));
    return () => {
      alive = false;
    };
  }, [kind, itemId, days, t]);

  const back = (
    <Link href={backHref(kind, itemId)}
      className="inline-flex items-center gap-1.5 text-[12.5px] text-[var(--muted-foreground)] hover:text-[var(--foreground)]">
      <ArrowLeft size={14} aria-hidden />
      {kind === "course" ? t("Courses") : t("Learning market")}
    </Link>
  );
  if (error && !report) {
    return <div className="space-y-4">{back}<p role="alert" className="text-[13px] text-red-600">{error}</p></div>;
  }
  if (!report) return <div className="space-y-4">{back}<div aria-busy="true" className="h-40 animate-pulse rounded-xl bg-[var(--muted)]" /></div>;

  const isCourse = report.kind === "course";
  const rows = onlyFlagged ? report.learners.filter((row) => row.flags.length > 0) : report.learners;
  const setTitles = new Map<string, string>();
  for (const row of report.learners) for (const set of row.practice_sets) setTitles.set(set.set_id, set.title);

  return (
    <article className="space-y-5" data-testid="learner-report">
      {back}
      <header className="space-y-1">
        <p className="text-[12px] text-[var(--muted-foreground)]">{kindLabel(report.kind, t)} · {t("Learning report")}</p>
        <h1 className={PAGE_TITLE_CLASS}>{report.title}</h1>
        <p className="text-[12.5px] text-[var(--muted-foreground)]">
          {t("{{count}} learners", { count: report.learners.length })}
        </p>
      </header>

      <div className="flex flex-wrap items-center gap-3 text-[12.5px]">
        <label className="flex items-center gap-1.5">
          {t("Period")}
          <select aria-label={t("Period")} className={selectClass} value={days ?? ""}
            onChange={(e) => setDays(e.target.value ? Number(e.target.value) : null)}>
            <option value="7">{t("Last 7 days")}</option>
            <option value="30">{t("Last 30 days")}</option>
            <option value="">{t("All time")}</option>
          </select>
        </label>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={onlyFlagged} onChange={(e) => setOnlyFlagged(e.target.checked)} />
          {t("Only learners who need attention")}
        </label>
        <DownloadButton download={() => downloadReport(kind, itemId, days)} label={t("Export CSV")} />
      </div>
      {error && <p role="alert" className="text-[12px] text-red-600">{error}</p>}

      {report.learners.length === 0 ? (
        <p className="text-[12.5px] text-[var(--muted-foreground)]">{t("Nobody has joined it yet.")}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px]">
            <thead className="text-[11.5px] text-[var(--muted-foreground)]">
              <tr>
                <th className={cell}>{t("Learner")}</th>
                <th className={cell}>{t("Needs attention")}</th>
                <th className={cell}>{t("Last studied")}</th>
                <th className={cell}>{t("Answers in period")}</th>
                {isCourse && <th className={cell}>{t("Tasks done in period")}</th>}
                {isCourse && <th className={cell}>{t("Tasks done")}</th>}
                <th className={cell}>{t("Mastered points")}</th>
                {isCourse && <th className={cell}>{t("Practice: best score")}</th>}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const points = mastered(row);
                const score = practiceScore(row);
                const open = selected === row.id;
                return (
                  <tr key={row.id} className={`border-t border-[var(--border)] ${open ? "bg-[var(--muted)]/40" : ""}`}>
                    <td className={`${cell} font-medium`}>
                      <button type="button" aria-expanded={open} className="text-[var(--foreground)] hover:underline"
                        onClick={() => setSelected(open ? null : row.id)}>
                        {row.username}
                      </button>
                    </td>
                    <td className={cell}>
                      <ul className="space-y-0.5">
                        {row.flags.map((flag, index) => (
                          <li key={index} className="text-[11.5px] text-amber-700 dark:text-amber-300">{flagText(flag, t)}</li>
                        ))}
                      </ul>
                    </td>
                    <td className={cell}>{when(row.overall.last_activity, t("Never"))}</td>
                    <td className={cell}>{row.overall.period_answers ?? 0}</td>
                    {isCourse && <td className={cell}>{row.overall.period_tasks_done ?? 0}</td>}
                    {isCourse && (
                      <td className={cell}>{row.tasks ? `${row.tasks.done} / ${row.tasks.total}` : "—"}</td>
                    )}
                    <td className={cell}>{points.total ? `${points.mastered} / ${points.total}` : "—"}</td>
                    {isCourse && <td className={cell}>{score ? `${score.score} / ${score.max}` : "—"}</td>}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {report.learners.length > 0 && (
        <p className="text-[11.5px] text-[var(--muted-foreground)]">
          {isCourse ? t("Only what happened in this course is shown.") : t("Only what happened in this path is shown.")}
        </p>
      )}
      <p className="text-[11.5px] text-[var(--muted-foreground)]">
        {t("Reports show progress numbers only. Learners' conversations and notes stay private.")}
      </p>

      {selected && <LearnerDetailView key={selected} kind={kind} itemId={itemId} userId={selected} />}

      {report.paths.map((path) => (
        <PathPoints key={path.template_id} kind={kind} itemId={itemId} templateId={path.template_id} title={path.title} />
      ))}

      {isCourse && report.practice_sets.length > 0 && (
        <section aria-labelledby="report-sets" className="space-y-2 rounded-xl border border-[var(--border)] p-3">
          <h2 id="report-sets" className="text-[13px] font-semibold">{t("Practice sets in this course")}</h2>
          <p className="text-[11.5px] text-[var(--muted-foreground)]">
            {t("Open a practice set's results to see the questions most got wrong and make remedial practice from them.")}
          </p>
          <ul className="space-y-1 text-[12.5px]">
            {report.practice_sets.map((setId) => (
              <li key={setId}>
                <Link href={practiceSetHref(setId, kind, itemId)} className="text-[var(--primary)] hover:underline">
                  {setTitles.get(setId) || t("Practice set")}
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}
    </article>
  );
}

/** One path of the item: who has mastered each point; remedial for the rest (§1, §4). */
function PathPoints({ kind, itemId, templateId, title }: { kind: ReportKind; itemId: string; templateId: string; title: string }) {
  const { t } = useTranslation();
  const openRemedial = useOpenRemedial(kind, itemId);
  const [data, setData] = useState<ReportPoints | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    getReportPoints(kind, itemId, templateId)
      .then((next) => alive && setData(next))
      .catch((err) => alive && setError(catalogError(err, t)));
    return () => {
      alive = false;
    };
  }, [kind, itemId, templateId, t]);

  return (
    <section aria-label={t("Knowledge points of {{title}}", { title })} className="space-y-2 rounded-xl border border-[var(--border)] p-3"
      data-testid="report-path-points">
      <h2 className="text-[13px] font-semibold">{t("Knowledge points of {{title}}", { title })}</h2>
      {error && <p role="alert" className="text-[12px] text-red-600">{error}</p>}
      {!data ? (
        !error && <div aria-busy="true" className="h-12 animate-pulse rounded-lg bg-[var(--muted)]" />
      ) : data.points.length === 0 ? (
        <p className="text-[12px] text-[var(--muted-foreground)]">{t("No learner has started it yet.")}</p>
      ) : (
        <ul className="space-y-1.5 text-[12px]">
          {data.points.map((point) => (
            <li key={point.id} className="flex flex-wrap items-center gap-2">
              <span className="min-w-0 flex-1 truncate">{point.name}</span>
              <span className="h-1.5 w-20 overflow-hidden rounded-full bg-[var(--muted)]">
                <span className="block h-full bg-emerald-500"
                  style={{ width: `${point.students ? Math.round((100 * point.mastered) / point.students) : 0}%` }} />
              </span>
              <span className="tabular-nums text-[var(--muted-foreground)]">
                {t("{{mastered}} / {{total}} mastered", { mastered: point.mastered, total: point.students })}
              </span>
              {point.not_mastered.length > 0 && (
                <button type="button" className={actionClass} disabled={busy !== ""}
                  aria-label={t("Remedial practice on {{name}} for the {{count}} who have not mastered it", {
                    name: point.name, count: point.not_mastered.length,
                  })}
                  onClick={() => {
                    setBusy(point.id);
                    setError("");
                    remedialFromPath(data.template_id, [point.id], point.not_mastered, t("Remedial: "))
                      .then(openRemedial)
                      .catch((err) => setError(catalogError(err, t)))
                      .finally(() => setBusy(""));
                  }}>
                  <Sparkles size={13} aria-hidden />
                  {busy === point.id
                    ? t("Generating…")
                    : t("Remedial practice for the {{count}} who have not mastered it", { count: point.not_mastered.length })}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** One learner on this item: each point's mastery, practice results, tasks. */
function LearnerDetailView({ kind, itemId, userId }: { kind: ReportKind; itemId: string; userId: string }) {
  const { t } = useTranslation();
  const openRemedial = useOpenRemedial(kind, itemId);
  const [detail, setDetail] = useState<LearnerDetail | null>(null);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [picked, setPicked] = useState<Record<string, string[]>>({});
  const [busy, setBusy] = useState(false);
  // Traceability #4: what the author already wrote to this learner, per path, read once.
  const [comments, setComments] = useState<Record<string, TeacherComment[]>>({});

  useEffect(() => {
    let alive = true;
    getLearnerDetail(kind, itemId, userId)
      .then((next) => {
        if (!alive) return;
        setDetail(next);
        for (const path of next.paths) {
          writtenComments("path_point", path.template_id, userId)
            .then((rows) => alive && setComments((cur) => ({ ...cur, [path.template_id]: rows })))
            .catch(() => alive && setComments((cur) => ({ ...cur, [path.template_id]: [] })));
        }
      })
      .catch((err) => alive && setError(catalogError(err, t)));
    return () => {
      alive = false;
    };
  }, [kind, itemId, userId, t]);

  if (error) return <p role="alert" className="text-[12px] text-red-600">{error}</p>;
  if (!detail) return <div aria-busy="true" className="h-20 animate-pulse rounded-xl bg-[var(--muted)]" />;
  return (
    <section aria-label={t("Details of {{name}}", { name: detail.username })}
      className="space-y-3 rounded-xl border border-[var(--border)] p-3" data-testid="learner-detail">
      <h2 className="text-[13px] font-semibold text-[var(--foreground)]">{detail.username}</h2>
      {actionError && <p role="alert" className="text-[12px] text-red-600">{actionError}</p>}
      {detail.tasks && (
        <p className="text-[12.5px] text-[var(--muted-foreground)]">
          {t("{{done}} / {{total}} tasks done", { done: detail.tasks.done, total: detail.tasks.total })}
        </p>
      )}
      {detail.practice_sets.length > 0 && (
        <ul className="space-y-1 text-[12.5px]">
          {detail.practice_sets.map((set) => (
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
      {detail.paths.length === 0 && detail.practice_sets.length === 0 && (
        <p className="text-[12px] text-[var(--muted-foreground)]">{t("Nothing learned here yet.")}</p>
      )}
      {detail.paths.map((path) => {
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
                <li key={kp.id || index} className="space-y-0.5 text-[12px]">
                  <div className="flex items-center gap-2">
                    {!kp.mastered && kp.id && (
                      <input type="checkbox" aria-label={t("Pick {{name}}", { name: kp.name })} checked={chosen.includes(kp.id)}
                        onChange={() => setPicked({
                          ...picked,
                          [path.template_id]: chosen.includes(kp.id) ? chosen.filter((k) => k !== kp.id) : [...chosen, kp.id],
                        })} />
                    )}
                    <span className="min-w-0 flex-1 truncate text-[var(--foreground)]">{kp.name}</span>
                    <span className="h-1.5 w-20 overflow-hidden rounded-full bg-[var(--muted)]">
                      <span className={`block h-full ${kp.mastered ? "bg-emerald-500" : "bg-[var(--primary)]"}`}
                        style={{ width: `${Math.round(kp.mastery * 100)}%` }} />
                    </span>
                    <span className="w-9 text-right tabular-nums text-[var(--muted-foreground)]">{Math.round(kp.mastery * 100)}%</span>
                  </div>
                  {(kp.answers ?? 0) > 0 && (
                    <p className="text-[11px] text-[var(--muted-foreground)]">
                      {t("{{answers}} answers, {{wrong}} wrong", { answers: kp.answers, wrong: kp.wrong ?? 0 })}
                      {kp.stuck_days != null && ` · ${t("stuck for {{days}} days", { days: kp.stuck_days })}`}
                    </p>
                  )}
                  {/* Traceability #4: a word to the learner on a point not mastered yet or stuck. */}
                  {kp.id && (!kp.mastered || kp.stuck_days != null) && (
                    <CommentEditor kind="path_point" subject={path.template_id} target={kp.id} studentId={userId}
                      preloaded={comments[path.template_id] ?? null} />
                  )}
                </li>
              ))}
            </ul>
            {chosen.length > 0 && (
              <button type="button" className={primaryClass} disabled={busy}
                onClick={() => {
                  setBusy(true);
                  setActionError("");
                  remedialFromPath(path.template_id, chosen, [userId], t("Remedial: "))
                    .then(openRemedial)
                    .catch((err) => setActionError(catalogError(err, t)))
                    .finally(() => setBusy(false));
                }}>
                <Sparkles size={13} aria-hidden />
                {busy ? t("Generating…") : t("Make remedial practice")}
              </button>
            )}
          </div>
        );
      })}
    </section>
  );
}
