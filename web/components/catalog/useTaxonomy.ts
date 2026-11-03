"use client";

import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { getAdminTaxonomy, getTaxonomy, type Taxonomy, type TaxonomyName } from "@/lib/catalog-api";

/**
 * One request per page load (platform batch 1 §6). Administrators may change it
 * (platform batch 2 §8): :func:`resetTaxonomyCache` makes the next reader fetch again.
 */
let cached: Promise<Taxonomy> | null = null;
/** Every entry, hidden ones too: names for ids the public classification no longer lists. */
let cachedAll: Promise<Taxonomy> | null = null;

export function loadTaxonomy(): Promise<Taxonomy> {
  if (!cached) {
    cached = getTaxonomy().catch((err) => {
      cached = null;
      throw err;
    });
  }
  return cached;
}

/** Administrators only; anyone else gets an empty classification (raw ids are shown). */
export function loadFullTaxonomy(): Promise<Taxonomy> {
  if (!cachedAll) {
    cachedAll = getAdminTaxonomy().catch(() => ({ stages: [], difficulties: [] }));
  }
  return cachedAll;
}

/** After an administrator's change, and for tests: the next reader fetches again. */
export function resetTaxonomyCache() {
  cached = null;
  cachedAll = null;
}

export interface TaxonomyIndexEntry {
  kind: "stage" | "subject" | "difficulty";
  names: TaxonomyName;
  stage?: string;
  parent?: string | null;
}

export function indexTaxonomy(taxonomy: Taxonomy | null): Map<string, TaxonomyIndexEntry> {
  const index = new Map<string, TaxonomyIndexEntry>();
  if (!taxonomy || !Array.isArray(taxonomy.stages)) return index;
  for (const stage of taxonomy.stages) {
    index.set(stage.id, { kind: "stage", names: stage.names });
    for (const subject of stage.subjects) {
      index.set(subject.id, { kind: "subject", names: subject.names, stage: stage.id, parent: null });
      for (const child of subject.children) {
        index.set(child.id, { kind: "subject", names: child.names, stage: stage.id, parent: subject.id });
      }
    }
  }
  for (const difficulty of taxonomy.difficulties ?? []) index.set(difficulty.id, { kind: "difficulty", names: difficulty.names });
  return index;
}

/** Chinese names for a Chinese interface, English ones otherwise. */
export const pickName = (names: TaxonomyName | undefined, language: string | undefined) =>
  names ? (language?.startsWith("zh") ? names.zh : names.en) : "";

/** ``enabled`` false: nothing is loaded (e.g. a panel that will not show). */
export function useTaxonomy(enabled = true) {
  const { i18n } = useTranslation();
  const [taxonomy, setTaxonomy] = useState<Taxonomy | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    loadTaxonomy()
      .then((next) => alive && setTaxonomy(next))
      .catch((err) => alive && setError(err instanceof Error ? err.message : String(err)));
    return () => {
      alive = false;
    };
  }, [enabled]);
  const index = useMemo(() => indexTaxonomy(taxonomy), [taxonomy]);
  const language = i18n?.language;
  const nameOf = useMemo(
    () => (id: string) => {
      const found = index.get(id);
      return found ? pickName(found.names, language) : id;
    },
    [index, language],
  );
  return { taxonomy, index, nameOf, error, language };
}
