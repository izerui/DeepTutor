"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { actionClass, inputClass, primaryClass } from "@/components/common/form-styles";
import {
  approveListing,
  classifyListing,
  getReviewItem,
  listReviewListed,
  listReviewQueue,
  rejectListing,
  takeDownListing,
  type CatalogEntry,
  type ReviewItem,
} from "@/lib/catalog-api";

import { AdminNav } from "./AdminNav";
import { authorLabel, catalogError, formatDay, itemHref, kindLabel, STATUS_TONE, statusLabel } from "./catalog-format";
import { CatalogPreview } from "./CatalogPreview";
import { TagInput } from "./TagInput";
import { TaxonomyPicker } from "./TaxonomyPicker";
import { useTaxonomy } from "./useTaxonomy";

export const REASON_MAX = 500;
type Tab = "pending" | "listed";

function eventLabel(action: string, t: (key: string) => string): string {
  switch (action) {
    case "submitted":
      return t("Submitted");
    case "approved":
      return t("Approved");
    case "listed":
      return t("Listed");
    case "rejected":
      return t("Not approved");
    case "taken_down":
      return t("Removed by the platform");
    case "unlisted":
      return t("Taken out of the market");
    case "relisted":
      return t("Put back");
    case "cancelled":
      return t("Request withdrawn");
    case "closed":
      return t("Closed by the author");
    default:
      return action;
  }
}

/**
 * Platform batch 2 §8.3: an administrator corrects the stages, subjects,
 * difficulty and tags of a listing in the market; never its title, summary or content.
 */
export function ClassificationEditor({
  item,
  onSaved,
  onCancel,
}: {
  item: CatalogEntry;
  onSaved: (next: CatalogEntry) => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const { taxonomy, error: taxonomyError } = useTaxonomy();
  const [fields, setFields] = useState({
    stages: item.stages ?? [],
    subjects: item.subjects ?? [],
    grades: item.grades ?? [],
    difficulty: item.difficulty ?? "",
    tags: item.tags ?? [],
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const save = async () => {
    setBusy(true);
    setError("");
    try {
      onSaved(await classifyListing(item.kind, item.id, fields));
    } catch (err) {
      setError(catalogError(err, t));
    } finally {
      setBusy(false);
    }
  };
  return (
    <fieldset disabled={busy} className="space-y-3 rounded-xl border border-[var(--border)] p-3" data-testid="classification-editor">
      <legend className="px-1 text-[12.5px] font-medium">{t("Edit classification")}</legend>
      {taxonomy ? (
        <TaxonomyPicker
          taxonomy={taxonomy}
          value={{ stages: fields.stages, subjects: fields.subjects, grades: fields.grades, difficulty: fields.difficulty }}
          onChange={(next) => setFields({ ...fields, ...next })}
        />
      ) : taxonomyError ? (
        <p role="alert" className="text-[12px] text-red-600">{t(taxonomyError)}</p>
      ) : null}
      <div className="space-y-1">
        <span className="text-[12.5px] font-medium">{t("Tags")}</span>
        <TagInput value={fields.tags} onChange={(tags) => setFields({ ...fields, tags })} />
      </div>
      <div className="flex flex-wrap gap-2">
        <button type="button" className={primaryClass} onClick={() => void save()}>{t("Save")}</button>
        <button type="button" className={actionClass} onClick={onCancel}>{t("Cancel")}</button>
      </div>
      {error && <p role="alert" className="text-[12px] text-red-600">{error}</p>}
    </fieldset>
  );
}

/** One listing under review: what the author wrote, the whole content read-only, and the decision. */
export function ReviewDetail({
  entry,
  onDone,
  canClassify = false,
}: {
  entry: CatalogEntry;
  onDone: () => void;
  /** The "In the market" tab: the administrator may correct the classification. */
  canClassify?: boolean;
}) {
  const { t } = useTranslation();
  const { nameOf, classificationOf } = useTaxonomy();
  const [classifying, setClassifying] = useState(false);
  const [item, setItem] = useState<ReviewItem | null>(null);
  const [reason, setReason] = useState("");
  const [deciding, setDeciding] = useState<"reject" | "take-down" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    setItem(null);
    setDeciding(null);
    setReason("");
    getReviewItem(entry.kind, entry.id)
      .then((next) => alive && setItem(next))
      .catch((err) => alive && setError(catalogError(err, t)));
    return () => {
      alive = false;
    };
  }, [entry.kind, entry.id, t]);

  const run = async (work: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await work();
      onDone();
    } catch (err) {
      setError(catalogError(err, t));
    } finally {
      setBusy(false);
    }
  };

  if (!item) {
    return error ? <p role="alert" className="text-[12px] text-red-600">{error}</p> : <div aria-busy="true" className="h-40 animate-pulse rounded-xl bg-[var(--muted)]" />;
  }
  const trimmed = reason.trim();
  const classification = classificationOf(item);
  return (
    <div className="space-y-4" data-testid="review-detail">
      <div className="space-y-1">
        <p className="flex flex-wrap items-center gap-2 text-[12px] text-[var(--muted-foreground)]">
          {kindLabel(item.kind, t)}
          <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${STATUS_TONE[item.status] ?? ""}`}>{statusLabel(item.status, t)}</span>
          {item.status === "listed" && <Link href={itemHref(item.kind, item.id)} className="text-[var(--primary)] hover:underline">{t("Open in the market")}</Link>}
        </p>
        <h2 className="text-[16px] font-semibold">{item.title}</h2>
        <p className="text-[12px] text-[var(--muted-foreground)]">
          {authorLabel(item, t)}
          {item.submitted_at ? ` · ${t("Submitted {{date}}", { date: formatDay(item.submitted_at) })}` : ""}
          {` · ${t("{{count}} learners", { count: item.learners ?? 0 })}`}
        </p>
      </div>
      <p className="whitespace-pre-line text-[13px]">{item.summary}</p>
      <dl className="grid gap-x-6 gap-y-1 text-[12.5px] sm:grid-cols-[max-content_1fr]">
        <dt className="text-[var(--muted-foreground)]">{t("Classification")}</dt>
        <dd>{classification || t("Needs classification")}</dd>
        <dt className="text-[var(--muted-foreground)]">{t("Difficulty")}</dt>
        <dd>{item.difficulty ? nameOf(item.difficulty) : "—"}</dd>
        <dt className="text-[var(--muted-foreground)]">{t("Tags")}</dt>
        <dd>{item.tags.length ? item.tags.map((tag) => `#${tag}`).join(" ") : "—"}</dd>
      </dl>
      {canClassify && (classifying ? (
        <ClassificationEditor
          item={item}
          onCancel={() => setClassifying(false)}
          onSaved={(next) => {
            setItem({ ...item, ...next, preview: item.preview, events: item.events });
            setClassifying(false);
            getReviewItem(item.kind, item.id).then(setItem).catch(() => undefined);
          }}
        />
      ) : (
        <button type="button" className={actionClass} onClick={() => setClassifying(true)}>{t("Edit classification")}</button>
      ))}

      <CatalogPreview kind={item.kind} itemId={item.id} preview={item.preview ?? {}} full />

      {item.events.length > 0 && (
        <details className="text-[12px]">
          <summary className="cursor-pointer text-[var(--muted-foreground)]">{t("History")}</summary>
          <ul className="mt-1 space-y-0.5">
            {item.events.map((ev, i) => (
              <li key={i}>
                {formatDay(ev.at)} · {eventLabel(ev.action, t)}
                {ev.reason ? `: ${ev.reason}` : ""}
              </li>
            ))}
          </ul>
        </details>
      )}

      <fieldset disabled={busy} className="space-y-2 border-t border-[var(--border)] pt-3">
        {deciding ? (
          <>
            <label className="block space-y-1">
              <span className="text-[12.5px] font-medium">
                {deciding === "reject" ? t("Why is it not approved?") : t("Why is it taken out of the market?")}
              </span>
              <textarea className={`${inputClass} h-20 py-2`} value={reason} maxLength={REASON_MAX}
                onChange={(e) => setReason(e.target.value)} />
            </label>
            {!trimmed && <p className="text-[11.5px] text-[var(--muted-foreground)]">{t("A reason is required; the author sees it.")}</p>}
            <div className="flex flex-wrap gap-2">
              <button type="button" className={primaryClass} disabled={!trimmed}
                onClick={() => void run(() => (deciding === "reject" ? rejectListing : takeDownListing)(item.kind, item.id, trimmed))}>
                {deciding === "reject" ? t("Reject") : t("Take down")}
              </button>
              <button type="button" className={actionClass} onClick={() => setDeciding(null)}>{t("Cancel")}</button>
            </div>
          </>
        ) : (
          <div className="flex flex-wrap gap-2">
            {item.status === "pending" && (
              <>
                <button type="button" className={primaryClass} onClick={() => void run(() => approveListing(item.kind, item.id))}>{t("Approve")}</button>
                <button type="button" className={actionClass} onClick={() => setDeciding("reject")}>{t("Reject")}</button>
              </>
            )}
            {(item.status === "listed" || item.status === "unlisted") && (
              <button type="button" className={actionClass} onClick={() => setDeciding("take-down")}>{t("Take down")}</button>
            )}
          </div>
        )}
      </fieldset>
      {error && <p role="alert" className="text-[12px] text-red-600">{error}</p>}
    </div>
  );
}

/** The market review (§5.1): the queue, oldest first, and everything in the market. */
export default function ReviewDesk() {
  const { t } = useTranslation();
  const [tab, setTab] = useState<Tab>("pending");
  const [selected, setSelected] = useState<CatalogEntry | null>(null);
  const [q, setQ] = useState("");
  const [search, setSearch] = useState("");
  const [refresh, setRefresh] = useState(0);
  // Only the answer for the list now asked for is shown.
  const key = `${tab}|${search}|${refresh}`;
  const [answer, setAnswer] = useState<{ key: string; rows: CatalogEntry[] | null; error: string } | null>(null);
  const rows = answer?.key === key ? answer.rows : null;
  const error = answer?.key === key ? answer.error : "";

  useEffect(() => {
    let alive = true;
    (tab === "pending" ? listReviewQueue() : listReviewListed(search))
      .then((next) => alive && setAnswer({ key, rows: next, error: "" }))
      .catch((err) => alive && setAnswer({ key, rows: null, error: catalogError(err, t) }));
    return () => {
      alive = false;
    };
  }, [key, tab, search, t]);

  return (
    <div className="h-screen overflow-y-auto bg-[var(--background)] px-4 py-10 [scrollbar-gutter:stable]">
      <div className="mx-auto max-w-5xl">
        <Link href="/" className="mb-4 inline-flex items-center gap-1.5 text-sm text-[var(--muted-foreground)] hover:text-[var(--foreground)]">
          <ArrowLeft size={16} />
          {t("Back")}
        </Link>
        <AdminNav refresh={refresh} />
        <h1 className="mb-4 font-serif text-xl font-semibold">{t("Listing review")}</h1>
        <div role="tablist" className="mb-4 flex gap-1.5">
          {(["pending", "listed"] as Tab[]).map((key) => (
            <button key={key} type="button" role="tab" aria-selected={tab === key}
              className={`rounded-lg px-3 py-1.5 text-[13px] ${tab === key ? "bg-[var(--muted)] font-medium" : "text-[var(--muted-foreground)] hover:bg-[var(--muted)]"}`}
              onClick={() => {
                setTab(key);
                setSelected(null);
              }}>
              {key === "pending" ? t("Waiting for review") : t("In the market")}
            </button>
          ))}
        </div>
        <div className="flex flex-col gap-6 md:flex-row">
          <div className="space-y-2 md:w-80 md:shrink-0">
            {tab === "listed" && (
              <form role="search" onSubmit={(e) => {
                e.preventDefault();
                setSearch(q);
              }}>
                <input className={inputClass} value={q} onChange={(e) => setQ(e.target.value)} aria-label={t("Search the market")} placeholder={t("Search the market")} />
              </form>
            )}
            {error && <p role="alert" className="text-[12px] text-red-600">{error}</p>}
            {rows === null ? (
              !error && <div aria-busy="true" className="h-24 animate-pulse rounded-xl bg-[var(--muted)]" />
            ) : rows.length === 0 ? (
              <p className="text-[12.5px] text-[var(--muted-foreground)]">
                {tab === "pending" ? t("Nothing waits for review.") : t("Nothing found.")}
              </p>
            ) : (
              <ul className="divide-y divide-[var(--border)] rounded-xl border border-[var(--border)]" aria-label={t("Listings")}>
                {rows.map((row) => {
                  const on = selected?.kind === row.kind && selected.id === row.id;
                  return (
                    <li key={`${row.kind}:${row.id}`}>
                      <button type="button" aria-current={on ? "true" : undefined}
                        className={`w-full space-y-0.5 p-2.5 text-left text-[12.5px] hover:bg-[var(--muted)]/40 ${on ? "bg-[var(--muted)]/60" : ""}`}
                        onClick={() => setSelected(row)}>
                        <span className="block truncate font-medium">{row.title}</span>
                        <span className="block text-[11px] text-[var(--muted-foreground)]">
                          {kindLabel(row.kind, t)} · {authorLabel(row, t)}
                          {tab === "pending" && row.submitted_at ? ` · ${formatDay(row.submitted_at)}` : ""}
                          {tab === "listed" ? ` · ${statusLabel(row.status, t)}` : ""}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
          <div className="min-w-0 flex-1">
            {selected ? (
              <ReviewDetail key={`${selected.kind}:${selected.id}`} entry={selected} canClassify={tab === "listed"} onDone={() => {
                setSelected(null);
                setRefresh((v) => v + 1);
              }} />
            ) : (
              <p className="text-[12.5px] text-[var(--muted-foreground)]">{t("Open a listing to read it and decide.")}</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
