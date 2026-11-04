/**
 * Where an item comes from (spec 2026-11-02 §3.1). The server decides
 * (``provenance`` on every list item); the client only labels and groups.
 */
export type ProvenanceKind = "own" | "school" | "teacher" | "shared";

export interface Provenance {
  kind: ProvenanceKind;
  name: string;
}

type Translate = (key: string, values?: Record<string, unknown>) => string;

/** Order of the groups in every list: what was handed out first, then one's own. */
export const PROVENANCE_ORDER: readonly ProvenanceKind[] = ["teacher", "school", "own", "shared"];

/**
 * The label for an item. What a teacher published reads "Published by tina"
 * for everyone (follow-ups §7); an administrator's, "Published by the platform".
 * ``viewerIsStudent`` only changes the group headings.
 */
export function provenanceLabel(provenance: Provenance | null | undefined, t: Translate, _viewerIsStudent?: boolean): string {
  switch (provenance?.kind ?? "own") {
    case "school":
      return t("Published by the platform");
    case "teacher":
      return t("Published by {{name}}", { name: provenance?.name ?? "" });
    case "shared":
      return t("Shared by {{name}}", { name: provenance?.name ?? "" });
    default:
      return t("Mine");
  }
}

/** The group heading for one kind of provenance. */
export function provenanceGroupTitle(kind: ProvenanceKind, t: Translate, viewerIsStudent: boolean): string {
  switch (kind) {
    case "teacher":
      return viewerIsStudent ? t("From my teachers") : t("From other teachers");
    case "school":
      return t("Published by the platform");
    case "shared":
      return t("Shared with me");
    default:
      return t("Mine");
  }
}

/** Items split by provenance, in :data:`PROVENANCE_ORDER`; empty groups are left out. */
export function groupByProvenance<T>(
  items: readonly T[],
  provenanceOf: (item: T) => Provenance | null | undefined,
): { kind: ProvenanceKind; items: T[] }[] {
  const groups = new Map<ProvenanceKind, T[]>(PROVENANCE_ORDER.map((kind) => [kind, []]));
  for (const item of items) groups.get(provenanceOf(item)?.kind ?? "own")?.push(item);
  return PROVENANCE_ORDER.map((kind) => ({ kind, items: groups.get(kind) ?? [] })).filter(
    (group) => group.items.length > 0,
  );
}
