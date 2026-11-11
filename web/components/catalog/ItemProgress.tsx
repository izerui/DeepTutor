"use client";

import Link from "next/link";
import { useTranslation } from "react-i18next";

import { actionClass, primaryClass } from "@/components/common/form-styles";
import type { CatalogItemDetail, ItemProgress, ItemStats } from "@/lib/catalog-api";

import { formatRating } from "./Reviews";

type Translate = (key: string, values?: Record<string, unknown>) => string;

function progressText(progress: ItemProgress, t: Translate): string {
  switch (progress.unit) {
    case "lessons":
      return t("{{done}} / {{total}} lessons done", { done: progress.done, total: progress.total });
    case "points":
      return t("{{done}} / {{total}} knowledge points mastered", { done: progress.done, total: progress.total });
    case "pages":
      return t("{{done}} / {{total}} pages read", { done: progress.done, total: progress.total });
    default: {
      const submissions = progress.submissions ?? 0;
      if (!submissions) return t("Not answered yet");
      const count = t("Submissions: {{count}}", { count: submissions });
      return progress.best_score == null
        ? count
        : `${count} · ${t("Best score {{score}} / {{max}}", { score: progress.best_score, max: progress.max_score })}`;
    }
  }
}

/** Spec 2026-11-11 §1: one's own progress above "Continue studying", and "Finished" once done. */
export function ProgressLine({ progress }: { progress: ItemProgress }) {
  const { t } = useTranslation();
  const bar = progress.unit !== "submissions";
  return (
    <div className="space-y-1.5" data-testid="item-progress">
      <p className="flex flex-wrap items-center gap-2 text-[12.5px] text-[var(--muted-foreground)]">
        <span>{progressText(progress, t)}</span>
        {progress.finished && (
          <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[11px] font-medium text-emerald-700 dark:text-emerald-300" data-testid="item-finished">
            {t("Finished")}
          </span>
        )}
      </p>
      {bar && (
        <div role="progressbar" aria-label={t("Your progress")} aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress.percent}
          className="h-1.5 max-w-sm overflow-hidden rounded-full bg-[var(--muted)]">
          <div className="h-full rounded-full bg-[var(--primary)]" style={{ width: `${progress.percent}%` }} />
        </div>
      )}
    </div>
  );
}

/** Spec 2026-11-11 §2: finished and not reviewed yet: ask for a review, or not now. */
export function FinishedBanner({ title, onRate, onLater }: { title: string; onRate: () => void; onLater: () => void }) {
  const { t } = useTranslation();
  return (
    <div role="status" className="flex flex-wrap items-center gap-2 rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-3 text-[13px]" data-testid="finished-banner">
      <span className="min-w-0 flex-1">{t("You finished “{{title}}”. How was it?", { title })}</span>
      <button type="button" className={primaryClass} onClick={onRate}>{t("Rate it")}</button>
      <button type="button" className={actionClass} onClick={onLater}>{t("Later")}</button>
    </div>
  );
}

/** Spec 2026-11-11 §5: the numbers its author (and administrators) see. */
export function StatsCard({ item, stats }: { item: CatalogItemDetail; stats: ItemStats }) {
  const { t } = useTranslation();
  const rows: [string, string | number][] = [
    [t("Learners"), stats.learners],
    [t("Joined in the last 7 days"), stats.recent_learners],
    ...(stats.finished == null ? [] : [[t("Finished it"), stats.finished] as [string, number]]),
    [t("Average rating"), stats.rating == null ? t("No rating yet") : formatRating(stats.rating)],
    [t("Reviews"), stats.rating_count],
    [t("Unanswered reviews"), stats.unanswered],
  ];
  return (
    <section aria-labelledby="item-stats" className="space-y-2 rounded-xl border border-[var(--border)] p-3" data-testid="item-stats">
      <div className="flex flex-wrap items-center gap-2">
        <h2 id="item-stats" className="text-[13px] font-semibold">{t("Numbers")}</h2>
        {item.kind === "practice_set" && (
          <Link href={`/practice-sets/results?set=${encodeURIComponent(item.id)}`} className="ml-auto text-[12px] text-[var(--primary)] hover:underline">
            {t("Results")}
          </Link>
        )}
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3">
        {rows.map(([label, value]) => (
          <div key={label}>
            <dt className="text-[11.5px] text-[var(--muted-foreground)]">{label}</dt>
            <dd className="text-[15px] font-semibold tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
