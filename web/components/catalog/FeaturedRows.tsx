"use client";

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { getFeatured, type CatalogEntry, type FeaturedItems } from "@/lib/catalog-api";

import { ItemCard } from "./ItemCard";

/** The featured lists, read once; ``null`` while loading or when they could not be read. */
export function useFeatured(enabled = true): FeaturedItems | null {
  const [featured, setFeatured] = useState<FeaturedItems | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    getFeatured()
      .then((next) => alive && setFeatured(next))
      .catch(() => alive && setFeatured(null));
    return () => {
      alive = false;
    };
  }, [enabled]);
  return featured;
}

/** One row of cards that scrolls sideways; nothing at all when empty. */
export function FeaturedRow({
  id,
  title,
  items,
  nameOf,
  classificationOf,
}: {
  id: string;
  title: string;
  items: CatalogEntry[];
  nameOf: (id: string) => string;
  classificationOf?: (entry: CatalogEntry) => string;
}) {
  if (items.length === 0) return null;
  return (
    <section aria-labelledby={id} className="space-y-2" data-testid={id}>
      <h2 id={id} className="text-[13.5px] font-semibold">{title}</h2>
      <ul className="flex gap-3 overflow-x-auto pb-1">
        {items.map((entry) => (
          <li key={`${entry.kind}:${entry.id}`} className="w-60 shrink-0">
            <ItemCard entry={entry} nameOf={nameOf} classificationOf={classificationOf} />
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Spec 2026-11-06 §3: above the market list when nothing is searched or filtered. */
export function FeaturedRows({
  featured,
  nameOf,
  classificationOf,
}: {
  featured: FeaturedItems | null;
  nameOf: (id: string) => string;
  classificationOf?: (entry: CatalogEntry) => string;
}) {
  const { t } = useTranslation();
  if (!featured || (featured.popular.length === 0 && featured.top_rated.length === 0)) return null;
  return (
    <div className="space-y-5" data-testid="market-featured">
      <FeaturedRow id="featured-popular" title={t("Most popular")} items={featured.popular}
        nameOf={nameOf} classificationOf={classificationOf} />
      <FeaturedRow id="featured-top-rated" title={t("Highly rated")} items={featured.top_rated}
        nameOf={nameOf} classificationOf={classificationOf} />
    </div>
  );
}
