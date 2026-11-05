"use client";

import { useTranslation } from "react-i18next";

import { provenanceLabel, type Provenance } from "@/lib/provenance";

const TONE: Record<Provenance["kind"], string> = {
  shared: "bg-sky-500/10 text-sky-700 dark:text-sky-300",
  school: "bg-violet-500/10 text-violet-700 dark:text-violet-300",
  own: "bg-[var(--muted)] text-[var(--muted-foreground)]",
};

/** Where an item comes from (spec 2026-11-02 §3.1), the same everywhere. */
export function SourceBadge({ provenance, className = "" }: { provenance?: Provenance | null; className?: string }) {
  const { t } = useTranslation();
  const kind = provenance?.kind ?? "own";
  return (
    <span
      data-provenance={kind}
      className={`inline-flex max-w-full items-center truncate rounded-full px-2 py-0.5 text-[11px] font-medium ${TONE[kind]} ${className}`}
    >
      {provenanceLabel(provenance, t)}
    </span>
  );
}
