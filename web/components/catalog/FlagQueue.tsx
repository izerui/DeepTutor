"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { actionClass, inputClass, primaryClass } from "@/components/common/form-styles";
import { handleFlags, listFlags, type FlagAction, type FlagGroup } from "@/lib/catalog-api";

import { catalogError, formatDay, itemHref, kindLabel, STATUS_TONE, statusLabel } from "./catalog-format";
import { flagReasonLabel } from "./ReportForm";

const REASON_MAX = 500;
const keyOf = (group: Pick<FlagGroup, "kind" | "id" | "review_user_id">) => `${group.kind}:${group.id}:${group.review_user_id}`;

/** One reported item or review: what was reported, by how many and why, and the decision. */
function FlagDetail({ group, onDone }: { group: FlagGroup; onDone: () => void }) {
  const { t } = useTranslation();
  const [takingDown, setTakingDown] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const trimmed = reason.trim();
  const canTakeDown = group.target === "item" && (group.status === "listed" || group.status === "unlisted");

  const run = async (action: FlagAction, why = "") => {
    setBusy(true);
    setError("");
    try {
      await handleFlags(group, action, why);
      onDone();
    } catch (err) {
      setError(catalogError(err, t));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4" data-testid="flag-detail">
      <div className="space-y-1">
        <p className="flex flex-wrap items-center gap-2 text-[12px] text-[var(--muted-foreground)]">
          {kindLabel(group.kind, t)}
          <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${STATUS_TONE[group.status] ?? ""}`}>{statusLabel(group.status, t)}</span>
          {(group.status === "listed" || group.status === "unlisted") && (
            <Link href={itemHref(group.kind, group.id)} className="text-[var(--primary)] hover:underline">{t("Open in the market")}</Link>
          )}
        </p>
        <h2 className="text-[16px] font-semibold">
          {group.target === "review" ? t("A review of “{{title}}”", { title: group.title }) : group.title}
        </h2>
        <p className="text-[12px] text-[var(--muted-foreground)]">
          {group.author ? `${t("By {{name}}", { name: group.author })} · ` : ""}
          {t("Reported {{count}} times", { count: group.count })}
        </p>
      </div>

      {group.review && (
        <blockquote className="rounded-lg border border-[var(--border)] p-3 text-[13px]" data-testid="flag-review">
          <p className="text-[12px] text-[var(--muted-foreground)]">
            {group.review.author || t("Learner")}
            {group.review.stars ? ` · ${t("{{count}} of 5 stars", { count: group.review.stars })}` : ""}
          </p>
          {group.review.exists ? (
            group.review.text ? <p className="whitespace-pre-line">{group.review.text}</p> : null
          ) : (
            <p className="text-[var(--muted-foreground)]">{t("This review was already deleted.")}</p>
          )}
        </blockquote>
      )}

      <dl className="flex flex-wrap gap-2 text-[12px]">
        {Object.entries(group.reasons).map(([reasonKey, count]) => (
          <div key={reasonKey} className="rounded-full bg-[var(--muted)] px-2 py-0.5">
            <dt className="inline">{flagReasonLabel(reasonKey, t)}</dt>
            <dd className="inline">{` · ${count}`}</dd>
          </div>
        ))}
      </dl>

      <ul className="space-y-1.5 text-[12.5px]" aria-label={t("Reported content")}>
        {group.reports.map((report, index) => (
          <li key={index} className="rounded-lg bg-[var(--muted)]/40 p-2">
            <p className="text-[11.5px] text-[var(--muted-foreground)]">
              {report.reporter || t("Learner")} · {flagReasonLabel(report.reason, t)} · {formatDay(report.created_at)}
            </p>
            {report.note && <p className="whitespace-pre-line">{report.note}</p>}
          </li>
        ))}
      </ul>

      <fieldset disabled={busy} className="space-y-2 border-t border-[var(--border)] pt-3">
        {takingDown ? (
          <>
            <label className="block space-y-1">
              <span className="text-[12.5px] font-medium">{t("Why is it taken out of the market?")}</span>
              <textarea className={`${inputClass} h-20 py-2`} value={reason} maxLength={REASON_MAX}
                onChange={(e) => setReason(e.target.value)} />
            </label>
            {!trimmed && <p className="text-[11.5px] text-[var(--muted-foreground)]">{t("A reason is required; the author sees it.")}</p>}
            <div className="flex flex-wrap gap-2">
              <button type="button" className={primaryClass} disabled={!trimmed} onClick={() => void run("take_down", trimmed)}>
                {t("Take down")}
              </button>
              <button type="button" className={actionClass} onClick={() => setTakingDown(false)}>{t("Cancel")}</button>
            </div>
          </>
        ) : (
          <div className="flex flex-wrap gap-2">
            {canTakeDown && (
              <button type="button" className={primaryClass} onClick={() => setTakingDown(true)}>{t("Take down")}</button>
            )}
            {group.target === "review" && group.review?.exists && (
              <button type="button" className={primaryClass}
                onClick={() => window.confirm(t("Delete this review?")) && void run("delete_review")}>
                {t("Delete review")}
              </button>
            )}
            <button type="button" className={actionClass} onClick={() => void run("dismiss")}>{t("Dismiss the reports")}</button>
          </div>
        )}
        <p className="text-[11.5px] text-[var(--muted-foreground)]">{t("Whoever reported it is told what you decided.")}</p>
      </fieldset>
      {error && <p role="alert" className="text-[12px] text-red-600">{error}</p>}
    </div>
  );
}

/** Audit fix B §1: the open reports, one row per reported item or review, the most reported first. */
export function FlagQueue({ onChanged }: { onChanged?: () => void }) {
  const { t } = useTranslation();
  const [groups, setGroups] = useState<FlagGroup[] | null>(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    let alive = true;
    listFlags()
      .then((next) => alive && (setGroups(next), setError("")))
      .catch((err) => alive && setError(catalogError(err, t)));
    return () => {
      alive = false;
    };
  }, [refresh, t]);

  const current = groups?.find((group) => keyOf(group) === selected) ?? null;
  return (
    <div className="flex flex-col gap-6 md:flex-row" data-testid="flag-queue">
      <div className="space-y-2 md:w-80 md:shrink-0">
        {error && <p role="alert" className="text-[12px] text-red-600">{error}</p>}
        {groups === null ? (
          !error && <div aria-busy="true" className="h-24 animate-pulse rounded-xl bg-[var(--muted)]" />
        ) : groups.length === 0 ? (
          <p className="text-[12.5px] text-[var(--muted-foreground)]">{t("No open reports.")}</p>
        ) : (
          <ul className="divide-y divide-[var(--border)] rounded-xl border border-[var(--border)]" aria-label={t("Reported content")}>
            {groups.map((group) => {
              const key = keyOf(group);
              const on = key === selected;
              return (
                <li key={key}>
                  <button type="button" aria-current={on ? "true" : undefined}
                    className={`w-full space-y-0.5 p-2.5 text-left text-[12.5px] hover:bg-[var(--muted)]/40 ${on ? "bg-[var(--muted)]/60" : ""}`}
                    onClick={() => setSelected(key)}>
                    <span className="flex items-center gap-1.5">
                      <span className="min-w-0 flex-1 truncate font-medium">{group.title}</span>
                      <span className="shrink-0 rounded-full bg-red-500/15 px-1.5 py-0.5 text-[10.5px] font-medium text-red-700 dark:text-red-300">
                        {group.count}
                      </span>
                    </span>
                    <span className="block text-[11px] text-[var(--muted-foreground)]">
                      {kindLabel(group.kind, t)} · {group.target === "review" ? t("A review") : t("Content")}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
      <div className="min-w-0 flex-1">
        {current ? (
          <FlagDetail key={keyOf(current)} group={current} onDone={() => {
            setSelected(null);
            setRefresh((v) => v + 1);
            onChanged?.();
          }} />
        ) : (
          <p className="text-[12.5px] text-[var(--muted-foreground)]">{t("Open a report to read it and decide.")}</p>
        )}
      </div>
    </div>
  );
}
