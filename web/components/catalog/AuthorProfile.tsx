"use client";

import { ArrowLeft, Star } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { PageHeader, pageGridClass } from "@/components/layout/FeaturePage";
import { CATALOG_KINDS, getAuthor, type AuthorPage } from "@/lib/catalog-api";

import { catalogError, kindLabel } from "./catalog-format";
import { ItemCard } from "./ItemCard";
import { formatRating } from "./Reviews";
import { useTaxonomy } from "./useTaxonomy";

const stat = "min-w-[9rem] rounded-xl border border-[var(--border)] px-3 py-2";
const statLabel = "text-[11.5px] text-[var(--muted-foreground)]";
const statValue = "mt-0.5 text-[14px] font-medium text-[var(--foreground)]";

/** An author's page (§5.1): their name, their items, ratings and learners, their items by kind. */
export default function AuthorProfile() {
  const { t } = useTranslation();
  const id = useSearchParams()?.get("id") ?? "";
  const [author, setAuthor] = useState<AuthorPage | null>(null);
  const [error, setError] = useState("");
  const { nameOf, classificationOf } = useTaxonomy();

  useEffect(() => {
    if (!id) return;
    let alive = true;
    getAuthor(id)
      .then((next) => alive && setAuthor(next))
      .catch((err) => alive && setError(catalogError(err, t)));
    return () => {
      alive = false;
    };
  }, [id, t]);

  const back = (
    <Link href="/market" className="mb-4 inline-flex items-center gap-1.5 text-[12.5px] text-[var(--muted-foreground)] hover:text-[var(--foreground)]">
      <ArrowLeft size={14} />
      {t("Learning market")}
    </Link>
  );
  if (!author) {
    return (
      <div>
        {back}
        {error || !id ? (
          <p role="alert" className="text-[13px] text-red-600">{error || t("Not found.")}</p>
        ) : (
          <div aria-busy="true" className="h-40 animate-pulse rounded-xl bg-[var(--muted)]" />
        )}
      </div>
    );
  }

  const name = author.role === "admin" ? t("Published by the platform") : author.name || t("Platform");
  return (
    <div data-testid="author-profile">
      {back}
      <PageHeader title={name} />
      {/* Spec 2026-11-06 §2: how much they offer, how it is rated, how many learn from it. */}
      <dl className="mb-6 flex flex-wrap gap-3" data-testid="author-stats">
        <div className={stat}>
          <dt className={statLabel}>{t("Items in the market")}</dt>
          <dd className={`${statValue} tabular-nums`}>{author.item_count ?? author.items.length}</dd>
        </div>
        <div className={stat}>
          <dt className={statLabel}>{t("Average rating")}</dt>
          <dd className={`${statValue} inline-flex items-center gap-1`}>
            {author.rating != null && author.rating_count > 0 ? (
              <>
                <Star size={14} aria-hidden className="text-amber-500" fill="currentColor" />
                {t("{{rating}} ({{count}} reviews)", { rating: formatRating(author.rating), count: author.rating_count })}
              </>
            ) : (
              <span className="text-[var(--muted-foreground)]">{t("Not rated yet")}</span>
            )}
          </dd>
        </div>
        <div className={stat}>
          <dt className={statLabel}>{t("Learners")}</dt>
          <dd className={`${statValue} tabular-nums`}>{author.learners}</dd>
        </div>
        {author.recent_learners !== undefined && (
          // Polish batch §2: new people over the last week, each counted once.
          <div className={stat}>
            <dt className={statLabel}>{t("Joined in the last 7 days")}</dt>
            <dd className={`${statValue} tabular-nums`} data-testid="author-recent-learners">{author.recent_learners}</dd>
          </div>
        )}
      </dl>
      {author.items.length === 0 ? (
        <p className="text-[13px] text-[var(--muted-foreground)]">{t("Nothing in the market yet.")}</p>
      ) : (
        <div className="space-y-6">
          {CATALOG_KINDS.map((kind) => {
            const items = author.items.filter((entry) => entry.kind === kind);
            if (items.length === 0) return null;
            return (
              <section key={kind} className="space-y-2">
                <h2 className="text-[13.5px] font-semibold">{kindLabel(kind, t)} <span className="font-normal text-[var(--muted-foreground)]">({items.length})</span></h2>
                <div className={pageGridClass(3)}>
                  {items.map((entry) => <ItemCard key={`${entry.kind}:${entry.id}`} entry={entry} nameOf={nameOf} classificationOf={classificationOf} />)}
                </div>
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}
