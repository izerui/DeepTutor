"use client";

import { BookOpen, ClipboardList, Database, GraduationCap, Layers, Star, Users, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { useTranslation } from "react-i18next";

import type { CatalogEntry } from "@/lib/catalog-api";

import { authorHref, authorLabel, itemHref, kindLabel } from "./catalog-format";
import { FavoriteButton } from "./FavoriteButton";
import { formatRating } from "./Reviews";

export const KIND_ICON: Record<string, LucideIcon> = {
  book: BookOpen,
  knowledge_base: Database,
  mastery_path: GraduationCap,
  practice_set: ClipboardList,
  course: Layers,
};

/**
 * One item in the market (§5.1): kind, title, author, classification, difficulty,
 * learners, joined; its rating and a favorite toggle (follow-ups §1).
 */
export function ItemCard({
  entry,
  nameOf,
  classificationOf,
  onFavoriteChanged,
}: {
  entry: CatalogEntry;
  nameOf: (id: string) => string;
  /** The classification line; stages, grades and subjects by name otherwise. */
  classificationOf?: (entry: CatalogEntry) => string;
  onFavoriteChanged?: (favorited: boolean) => void;
}) {
  const { t } = useTranslation();
  const Icon = KIND_ICON[entry.kind] ?? BookOpen;
  const classification = classificationOf
    ? classificationOf(entry)
    : [...entry.stages, ...(entry.grades ?? []), ...entry.subjects].map(nameOf).join(" · ");
  const rated = entry.rating != null && (entry.rating_count ?? 0) > 0;
  // The title link covers the card; the author's name links to their page above it (spec 2026-11-06 §2).
  return (
    <div className="relative flex min-w-0 flex-col gap-1.5 rounded-xl border border-[var(--border)] p-3 transition-colors hover:border-[var(--ring)]" data-testid="market-card">
      <span className="flex items-center gap-1.5 pr-7 text-[11px] text-[var(--muted-foreground)]">
        <Icon size={13} aria-hidden />
        {kindLabel(entry.kind, t)}
        {entry.joined && (
          <span className="ml-auto rounded-full bg-emerald-500/15 px-2 py-0.5 font-medium text-emerald-700 dark:text-emerald-300">
            {t("Joined")}
          </span>
        )}
      </span>
      <Link href={itemHref(entry.kind, entry.id)}
        className="line-clamp-2 text-[14px] font-medium text-[var(--foreground)] after:absolute after:inset-0 after:rounded-xl after:content-['']">
        {entry.title}
      </Link>
      {entry.owner_id ? (
        <Link href={authorHref(entry.owner_id)} className="relative z-[1] truncate text-[11.5px] text-[var(--muted-foreground)] hover:text-[var(--primary)] hover:underline">
          {authorLabel(entry, t)}
        </Link>
      ) : (
        <span className="truncate text-[11.5px] text-[var(--muted-foreground)]">{authorLabel(entry, t)}</span>
      )}
      {classification && <span className="truncate text-[11.5px] text-[var(--muted-foreground)]">{classification}</span>}
      <span className="mt-auto flex flex-wrap items-center gap-2 text-[11.5px] text-[var(--muted-foreground)]">
        {entry.difficulty && <span className="rounded-full bg-[var(--muted)] px-2 py-0.5">{nameOf(entry.difficulty)}</span>}
        <span className="inline-flex items-center gap-1">
          <Users size={12} aria-hidden />
          {t("{{count}} learners", { count: entry.learners ?? 0 })}
        </span>
        {entry.recent_learners !== undefined && (
          // Polish batch §2: on the author page, how many came in over the last week.
          <span data-testid="card-recent-learners">
            {t("{{count}} joined in the last 7 days", { count: entry.recent_learners })}
          </span>
        )}
        {rated && (
          <span className="inline-flex items-center gap-1" data-testid="card-rating"
            aria-label={t("Rated {{rating}} out of 5 by {{count}}", { rating: formatRating(entry.rating ?? 0), count: entry.rating_count })}>
            <Star size={12} aria-hidden className="text-amber-500" fill="currentColor" />
            <span aria-hidden>{formatRating(entry.rating ?? 0)} ({entry.rating_count})</span>
          </span>
        )}
      </span>
      <span className="absolute right-2 top-2 z-[1]">
        <FavoriteButton compact kind={entry.kind} itemId={entry.id} favorited={Boolean(entry.favorited)} onChanged={onFavoriteChanged} />
      </span>
    </div>
  );
}
