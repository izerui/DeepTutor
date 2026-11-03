"use client";

import { BookOpen, ClipboardList, Database, GraduationCap, Layers, Users, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { useTranslation } from "react-i18next";

import type { CatalogEntry } from "@/lib/catalog-api";

import { authorLabel, itemHref, kindLabel } from "./catalog-format";

export const KIND_ICON: Record<string, LucideIcon> = {
  book: BookOpen,
  knowledge_base: Database,
  mastery_path: GraduationCap,
  practice_set: ClipboardList,
  course: Layers,
};

/** One item in the market (§5.1): kind, title, author, classification, difficulty, learners, joined. */
export function ItemCard({ entry, nameOf }: { entry: CatalogEntry; nameOf: (id: string) => string }) {
  const { t } = useTranslation();
  const Icon = KIND_ICON[entry.kind] ?? BookOpen;
  const classification = [...entry.stages, ...entry.subjects].map(nameOf).join(" · ");
  return (
    <Link
      href={itemHref(entry.kind, entry.id)}
      className="flex min-w-0 flex-col gap-1.5 rounded-xl border border-[var(--border)] p-3 transition-colors hover:border-[var(--ring)]"
      data-testid="market-card"
    >
      <span className="flex items-center gap-1.5 text-[11px] text-[var(--muted-foreground)]">
        <Icon size={13} aria-hidden />
        {kindLabel(entry.kind, t)}
        {entry.joined && (
          <span className="ml-auto rounded-full bg-emerald-500/15 px-2 py-0.5 font-medium text-emerald-700 dark:text-emerald-300">
            {t("Joined")}
          </span>
        )}
      </span>
      <span className="line-clamp-2 text-[14px] font-medium text-[var(--foreground)]">{entry.title}</span>
      <span className="truncate text-[11.5px] text-[var(--muted-foreground)]">{authorLabel(entry, t)}</span>
      {classification && <span className="truncate text-[11.5px] text-[var(--muted-foreground)]">{classification}</span>}
      <span className="mt-auto flex items-center gap-2 text-[11.5px] text-[var(--muted-foreground)]">
        {entry.difficulty && <span className="rounded-full bg-[var(--muted)] px-2 py-0.5">{nameOf(entry.difficulty)}</span>}
        <span className="inline-flex items-center gap-1">
          <Users size={12} aria-hidden />
          {t("{{count}} learners", { count: entry.learners ?? 0 })}
        </span>
      </span>
    </Link>
  );
}
