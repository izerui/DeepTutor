"use client";

import { useTranslation } from "react-i18next";

type Translate = (key: string, values?: Record<string, unknown>) => string;
export type WithdrawnHow = "taken_down" | "archived" | null | undefined;

/**
 * Polish batch §1: why something one joined is marked in one's own list.
 * Taken down by an administrator, archived by its author (both: nothing left
 * to study, records kept), or — neither — taken out of the market by its
 * author (still studied).
 */
export function withdrawnLabel(unavailable: WithdrawnHow, t: Translate): string {
  if (unavailable === "taken_down") return t("Taken down");
  if (unavailable === "archived") return t("Archived by its author");
  return t("Taken out of the market by its author");
}

/** The sentence in place of "Go study" / "Start" when it can no longer be studied. */
export function withdrawnText(unavailable: WithdrawnHow, t: Translate): string {
  return unavailable === "taken_down"
    ? t("This was taken down. Your learning records are kept.")
    : t("Its author withdrew this. Your learning records are kept.");
}

export function WithdrawnBadge({ unavailable }: { unavailable: WithdrawnHow }) {
  const { t } = useTranslation();
  return (
    <span
      className="rounded-full bg-[var(--muted)] px-2 py-0.5 text-[11px] font-medium text-[var(--muted-foreground)]"
      data-testid="withdrawn-badge"
    >
      {withdrawnLabel(unavailable, t)}
    </span>
  );
}
