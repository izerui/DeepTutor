"use client";

import { ArrowLeft, Star, Users } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { actionClass, primaryClass } from "@/components/common/form-styles";
import { PAGE_TITLE_CLASS } from "@/components/layout/FeaturePage";
import { useAuthStatus } from "@/hooks/useAuthStatus";
import {
  CATALOG_KINDS,
  getCatalogItem,
  listNextItems,
  REPORT_KINDS,
  reportItem,
  type CatalogEntry,
  type CatalogItemDetail,
  type CatalogKind,
  type ReportKind,
} from "@/lib/catalog-api";

import { AnnouncementsPanel } from "./Announcements";
import { authorHref, authorLabel, catalogError, formatDay, kindLabel, openHref, STATUS_TONE, statusLabel } from "./catalog-format";
import { CatalogPreview } from "./CatalogPreview";
import { FavoriteButton } from "./FavoriteButton";
import { FeaturedRow } from "./FeaturedRows";
import { FinishedBanner, ProgressLine, StatsCard } from "./ItemProgress";
import { JoinButton } from "./JoinButton";
import { ListingPanel } from "./ListingPanel";
import { ReportForm } from "./ReportForm";
import { formatRating, ReviewsSection } from "./Reviews";
import { useTaxonomy } from "./useTaxonomy";

/**
 * Spec 2026-11-11 §3: below the introduction, what to study next (for who joined
 * it) or what is related; nothing at all when there is none.
 */
export function NextRow({ kind, itemId, joined, nameOf, classificationOf }: {
  kind: string;
  itemId: string;
  joined: boolean;
  nameOf: (id: string) => string;
  classificationOf?: (entry: CatalogEntry) => string;
}) {
  const { t } = useTranslation();
  const [items, setItems] = useState<CatalogEntry[]>([]);
  useEffect(() => {
    let alive = true;
    listNextItems(kind, itemId)
      .then((next) => alive && setItems(next))
      .catch(() => alive && setItems([]));
    return () => {
      alive = false;
    };
  }, [kind, itemId]);
  return (
    <FeaturedRow id="item-next" title={joined ? t("Next to study") : t("Related")} items={items}
      nameOf={nameOf} classificationOf={classificationOf} />
  );
}

/** An item's introduction (§5.1): ``/market/item?kind=…&id=…`` (``&manage=1`` opens the author's listing). */
export default function ItemIntro() {
  const { t } = useTranslation();
  const { isAdmin } = useAuthStatus();
  const params = useSearchParams();
  const kind = params?.get("kind") ?? "";
  const id = params?.get("id") ?? "";
  const [item, setItem] = useState<CatalogItemDetail | null>(null);
  const [error, setError] = useState("");
  const [managing, setManaging] = useState(params?.get("manage") === "1");
  const [reporting, setReporting] = useState(false);
  // Spec 2026-11-11 §2: "Later" puts the review prompt away for this visit only.
  const [later, setLater] = useState(false);
  const { nameOf, classificationOf } = useTaxonomy();

  const load = useCallback(() => {
    if (!kind || !id) return;
    getCatalogItem(kind, id)
      .then((next) => {
        setItem(next);
        setError("");
      })
      .catch((err) => setError(catalogError(err, t)));
  }, [kind, id, t]);
  useEffect(load, [load]);

  /** To the reviews, with the first star of the rating form in focus. */
  const openReviewForm = () => {
    const section = document.getElementById("reviews");
    section?.scrollIntoView?.({ behavior: "smooth", block: "start" });
    section?.querySelector<HTMLElement>('[data-testid="review-form"] [role="radio"]')?.focus({ preventScroll: true });
    setLater(true);
  };

  const back = (
    <Link href="/market" className="mb-4 inline-flex items-center gap-1.5 text-[12.5px] text-[var(--muted-foreground)] hover:text-[var(--foreground)]">
      <ArrowLeft size={14} />
      {t("Learning market")}
    </Link>
  );
  if (!kind || !id || !CATALOG_KINDS.includes(kind as CatalogKind)) {
    return <div>{back}<p className="text-[13px] text-[var(--muted-foreground)]">{t("Not found.")}</p></div>;
  }
  if (!item) {
    return (
      <div>
        {back}
        {error ? <p role="alert" className="text-[13px] text-red-600">{error}</p> : <div aria-busy="true" className="h-40 animate-pulse rounded-xl bg-[var(--muted)]" />}
      </div>
    );
  }

  const classification = classificationOf(item);
  const authorView = Boolean(item.author_view);
  const askReview = Boolean(item.progress?.finished && !item.reviewed && item.can_review && !later);
  // Platform batch 3 §3: a course's or a path's announcements, for who joined it and its author.
  const hasLearners = REPORT_KINDS.includes(item.kind as ReportKind);
  return (
    <article className="space-y-5" data-testid="item-intro">
      {back}
      {askReview && <FinishedBanner title={item.title} onRate={openReviewForm} onLater={() => setLater(true)} />}
      <header className="space-y-2">
        <p className="flex flex-wrap items-center gap-2 text-[12px] text-[var(--muted-foreground)]">
          <span>{kindLabel(item.kind, t)}</span>
          {item.status !== "listed" && (
            <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${STATUS_TONE[item.status] ?? ""}`}>{statusLabel(item.status, t)}</span>
          )}
        </p>
        <h1 className={PAGE_TITLE_CLASS}>{item.title}</h1>
        <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[12.5px] text-[var(--muted-foreground)]">
          <Link href={authorHref(item.owner_id)} className="text-[var(--primary)] hover:underline">{authorLabel(item, t)}</Link>
          <span className="inline-flex items-center gap-1"><Users size={12} aria-hidden />{t("{{count}} learners", { count: item.learners ?? 0 })}</span>
          {item.rating != null && (item.rating_count ?? 0) > 0 && (
            <span className="inline-flex items-center gap-1" data-testid="intro-rating">
              <Star size={12} aria-hidden className="text-amber-500" fill="currentColor" />
              {t("{{rating}} ({{count}} reviews)", { rating: formatRating(item.rating), count: item.rating_count })}
            </span>
          )}
          {item.updated_at ? <span>{t("Updated {{date}}", { date: formatDay(item.updated_at) })}</span> : null}
        </p>
      </header>

      {item.progress && item.joined && !item.closed && <ProgressLine progress={item.progress} />}
      <div className="flex flex-wrap items-center gap-2">
        <JoinButton item={item} onChanged={load} />
        {/* Spec 2026-11-11 §5: its author goes straight to the editor (or the content itself). */}
        {item.open && (
          <Link href={openHref(item.open)} className={primaryClass} data-testid="item-open">{t("Open")}</Link>
        )}
        {/* Spec 2026-11-11 §5: one does not keep one's own content in one's favorites. */}
        {!authorView && (item.status === "listed" || item.favorited) && (
          <FavoriteButton key={`${item.kind}:${item.id}:${Boolean(item.favorited)}`} kind={item.kind} itemId={item.id} favorited={Boolean(item.favorited)} />
        )}
        {item.mine && (
          <button type="button" className={actionClass} aria-expanded={managing} onClick={() => setManaging((v) => !v)}>
            {t("Manage listing")}
          </button>
        )}
        {/* Spec 2026-11-05: administrators read every report too (read-only). */}
        {(item.mine || isAdmin) && hasLearners && (
          <Link href={`/reports?kind=${encodeURIComponent(item.kind)}&id=${encodeURIComponent(item.id)}`} className={actionClass}>
            {t("Learning report")}
          </Link>
        )}
        {/* Audit fix B §1: anyone but its author reports it; an administrator decides. */}
        {item.can_report && !authorView && !item.reported && !reporting && (
          <button type="button" className={actionClass} onClick={() => setReporting(true)}>
            {t("Report this")}
          </button>
        )}
        {item.reported && (
          <span className="text-[12px] text-[var(--muted-foreground)]" data-testid="item-reported">
            {t("Reported. An administrator will look at it.")}
          </span>
        )}
      </div>
      {reporting && (
        <ReportForm
          label={t("Report this content")}
          onSend={(reason, note) => reportItem(item.kind, item.id, reason, note)}
          onCancel={() => setReporting(false)}
          onDone={() => {
            setReporting(false);
            setItem((cur) => (cur ? { ...cur, reported: true } : cur));
          }}
        />
      )}
      {item.mine && managing && <ListingPanel kind={item.kind} itemId={item.id} onChanged={load} />}
      {item.stats && <StatsCard item={item} stats={item.stats} />}

      {hasLearners && (item.mine || item.joined) && (
        <AnnouncementsPanel kind={item.kind as ReportKind} itemId={item.id} canPost={item.mine} />
      )}

      {item.summary && <p className="whitespace-pre-line text-[13.5px] leading-relaxed">{item.summary}</p>}

      <dl className="grid gap-x-6 gap-y-2 text-[12.5px] sm:grid-cols-[max-content_1fr]">
        {classification && (
          <>
            <dt className="text-[var(--muted-foreground)]">{t("Classification")}</dt>
            <dd>{classification}</dd>
          </>
        )}
        {item.difficulty && (
          <>
            <dt className="text-[var(--muted-foreground)]">{t("Difficulty")}</dt>
            <dd>{nameOf(item.difficulty)}</dd>
          </>
        )}
        {item.tags.length > 0 && (
          <>
            <dt className="text-[var(--muted-foreground)]">{t("Tags")}</dt>
            <dd className="flex flex-wrap gap-1">
              {item.tags.map((tag) => (
                <Link key={tag} href={`/market?tag=${encodeURIComponent(tag)}`} className="rounded-full bg-[var(--muted)] px-2 py-0.5 text-[11.5px] hover:underline">
                  #{tag}
                </Link>
              ))}
            </dd>
          </>
        )}
      </dl>

      <section aria-label={t("Preview")}>
        <CatalogPreview kind={item.kind} itemId={item.id} preview={item.preview ?? {}} canTry={Boolean(item.can_try)} />
      </section>

      <ReviewsSection kind={item.kind} itemId={item.id}
        onRated={(next) => setItem((cur) => (cur ? { ...cur, rating: next.average, rating_count: next.count, reviewed: Boolean(next.mine) } : cur))} />

      <NextRow kind={item.kind} itemId={item.id} joined={Boolean(item.joined)} nameOf={nameOf} classificationOf={classificationOf} />
    </article>
  );
}
