"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { actionClass, inputClass, primaryClass } from "@/components/common/form-styles";
import { useAuthStatus } from "@/hooks/useAuthStatus";
import {
  CatalogApiError,
  getListing,
  listingAction,
  saveListing,
  type AuthorListing,
  type CatalogKind,
  type ListingAction,
  type ListingFields,
  type ListingStatus,
} from "@/lib/catalog-api";

import { catalogError, itemHref, STATUS_TONE, statusLabel } from "./catalog-format";
import { TagInput } from "./TagInput";
import { MAX_GRADES, MAX_STAGES, MAX_SUBJECTS, TaxonomyPicker } from "./TaxonomyPicker";
import { useTaxonomy } from "./useTaxonomy";

export const TITLE_MAX = 80;
export const SUMMARY_MAX = 1000;

type Translate = (key: string, values?: Record<string, unknown>) => string;

/** Which buttons a listing in this state offers (§2). ``submit`` saves the form first. */
export function listingButtons(status: ListingStatus, approvedOnce: boolean): ListingAction[] {
  switch (status) {
    case "pending":
      return ["withdraw-request"];
    case "listed":
      return ["unlist"];
    case "unlisted":
      return approvedOnce ? ["relist"] : ["submit"];
    default:
      return ["submit"];
  }
}

export function actionLabel(action: ListingAction, isAdmin: boolean, t: Translate): string {
  switch (action) {
    case "submit":
      return isAdmin ? t("List now") : t("Submit for review");
    case "withdraw-request":
      return t("Withdraw request");
    case "unlist":
      return t("Take out of the market");
    case "relist":
      return t("Put back");
  }
}

/** What still keeps the form from being submitted; empty when complete. */
export function missingField(fields: ListingFields, t: Translate): string {
  if (!fields.title.trim()) return t("Give it a title.");
  if (fields.title.trim().length > TITLE_MAX) return t("The title has at most 80 characters.");
  if (!fields.summary.trim()) return t("Write an introduction.");
  if (fields.summary.trim().length > SUMMARY_MAX) return t("The introduction has at most 1000 characters.");
  if (fields.stages.length === 0) return t("Choose at least one stage.");
  if (fields.subjects.length === 0) return t("Choose at least one subject.");
  if (fields.stages.length > MAX_STAGES || fields.subjects.length > MAX_SUBJECTS || fields.grades.length > MAX_GRADES) return t("Choose at most 3 of each.");
  if (!fields.difficulty) return t("Choose a difficulty.");
  return "";
}

const fieldsOf = (listing: AuthorListing): ListingFields => ({
  title: listing.title ?? "",
  summary: listing.summary ?? "",
  stages: listing.stages ?? [],
  subjects: listing.subjects ?? [],
  grades: listing.grades ?? [],
  difficulty: listing.difficulty ?? "",
  tags: listing.tags ?? [],
});

/**
 * The author's listing (platform batch 1 §5.2): its state, the latest reason
 * it was turned down, the market information, the buttons for this state
 * and how many have joined. Only teachers and administrators list content.
 */
export function ListingPanel({ kind, itemId, onChanged }: { kind: CatalogKind; itemId: string; onChanged?: () => void }) {
  const { t } = useTranslation();
  const { isAdmin, isTeacher, enabled, loading } = useAuthStatus();
  const [listing, setListing] = useState<AuthorListing | null>(null);
  const [fields, setFields] = useState<ListingFields | null>(null);
  const [hidden, setHidden] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const allowed = !enabled || isTeacher || isAdmin;
  const { taxonomy, error: taxonomyError } = useTaxonomy(!loading && allowed && !hidden);

  useEffect(() => {
    if (loading || !allowed) return;
    let alive = true;
    setHidden(false);
    getListing(kind, itemId)
      .then((next) => {
        if (!alive) return;
        setListing(next);
        setFields(fieldsOf(next));
      })
      .catch((err) => {
        if (!alive) return;
        if (err instanceof CatalogApiError && (err.status === 403 || err.status === 404)) setHidden(true);
        else setError(catalogError(err, t));
      });
    return () => {
      alive = false;
    };
  }, [kind, itemId, loading, allowed, t]);

  if (loading || !allowed || hidden) return null;
  if (!listing || !fields) {
    return error ? (
      <p role="alert" className="text-[12px] text-red-600">{error}</p>
    ) : (
      <div aria-busy="true" className="h-24 animate-pulse rounded-xl bg-[var(--muted)]" />
    );
  }

  const dirty = JSON.stringify(fields) !== JSON.stringify(fieldsOf(listing));
  const missing = missingField(fields, t);
  const buttons = listingButtons(listing.status, listing.approved_once);

  const run = async (work: () => Promise<AuthorListing>, message: string) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const next = await work();
      setListing(next);
      setFields(fieldsOf(next));
      setNotice(message);
      onChanged?.();
    } catch (err) {
      setError(catalogError(err, t));
    } finally {
      setBusy(false);
    }
  };
  const save = () => saveListing(kind, itemId, fields);
  const act = (action: ListingAction) =>
    run(async () => {
      if (action === "submit" && (dirty || listing.status === "none")) await save();
      return listingAction(kind, itemId, action);
    }, action === "submit" ? (isAdmin ? t("It is in the market now.") : t("Submitted for review.")) : t("Done."));

  const showReason = (listing.status === "rejected" || listing.status === "taken_down") && listing.reason;
  const submitBlocked = Boolean(listing.not_ready) || Boolean(missing);

  return (
    <section aria-labelledby={`listing-${kind}-${itemId}`} className="space-y-3 rounded-xl border border-[var(--border)] p-3" data-testid="listing-panel">
      <div className="flex flex-wrap items-center gap-2">
        <h3 id={`listing-${kind}-${itemId}`} className="text-[13px] font-semibold">{t("Learning market")}</h3>
        <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${STATUS_TONE[listing.status] ?? ""}`} data-testid="listing-status">
          {statusLabel(listing.status, t)}
        </span>
        <span className="text-[11.5px] text-[var(--muted-foreground)]">
          {t("{{count}} learners", { count: listing.learners ?? 0 })}
        </span>
        {listing.status === "listed" && (
          <Link href={itemHref(kind, itemId)} className="ml-auto text-[12px] text-[var(--primary)] hover:underline">
            {t("Open in the market")}
          </Link>
        )}
      </div>
      <p className="text-[11.5px] text-[var(--muted-foreground)]">
        {t("Nobody receives it until they join from the learning market.")}
      </p>
      {showReason && (
        <p role="note" className="rounded-lg bg-red-500/10 p-2 text-[12px] text-red-700 dark:text-red-300">
          {listing.status === "rejected"
            ? t("Not approved: {{reason}}", { reason: listing.reason })
            : t("Taken out of the market by the platform: {{reason}}", { reason: listing.reason })}
        </p>
      )}

      <fieldset disabled={busy} className="min-w-0 space-y-3">
        <label className="block space-y-1">
          <span className="text-[12.5px] font-medium">{t("Title")}</span>
          <input className={inputClass} value={fields.title} maxLength={TITLE_MAX}
            onChange={(e) => setFields({ ...fields, title: e.target.value })} />
        </label>
        <label className="block space-y-1">
          <span className="text-[12.5px] font-medium">{t("Introduction")}</span>
          <textarea className="min-h-20 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] p-2 text-[13px] outline-none focus:border-[var(--ring)]"
            value={fields.summary} maxLength={SUMMARY_MAX}
            onChange={(e) => setFields({ ...fields, summary: e.target.value })} />
          <span className="block text-right text-[11px] text-[var(--muted-foreground)]">{fields.summary.length} / {SUMMARY_MAX}</span>
        </label>
        {taxonomy ? (
          <TaxonomyPicker
            taxonomy={taxonomy}
            value={{ stages: fields.stages, subjects: fields.subjects, grades: fields.grades, difficulty: fields.difficulty }}
            onChange={(next) => setFields({ ...fields, ...next })}
          />
        ) : taxonomyError ? (
          <p role="alert" className="text-[12px] text-red-600">{taxonomyError}</p>
        ) : null}
        <div className="space-y-1">
          <span className="text-[12.5px] font-medium">{t("Tags")}</span>
          <TagInput value={fields.tags} onChange={(tags) => setFields({ ...fields, tags })} />
        </div>
      </fieldset>

      {listing.not_ready && <p className="text-[12px] text-amber-700 dark:text-amber-300">{t(listing.not_ready)}</p>}
      {!listing.not_ready && missing && buttons.includes("submit") && (
        <p className="text-[12px] text-[var(--muted-foreground)]">{missing}</p>
      )}

      <div className="flex flex-wrap gap-2">
        <button type="button" className={actionClass}
          disabled={busy || (!dirty && listing.status !== "none") || Boolean(missing && listing.status === "listed")}
          onClick={() => void run(save, t("Saved."))}>
          {t("Save")}
        </button>
        {buttons.map((action) => (
          <button key={action} type="button" className={action === "submit" || action === "relist" ? primaryClass : actionClass}
            disabled={busy || (action === "submit" && submitBlocked)}
            onClick={() => void act(action)}>
            {actionLabel(action, isAdmin, t)}
          </button>
        ))}
      </div>
      {error && <p role="alert" className="text-[12px] text-red-600">{error}</p>}
      {notice && <p role="status" className="text-[12px] text-[var(--muted-foreground)]">{notice}</p>}
    </section>
  );
}
