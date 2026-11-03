"use client";

import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { getTaxonomy, type Taxonomy, type TaxonomyName } from "@/lib/catalog-api";

/** The classification is built in and read-only (platform batch 1 §6): one request per page load. */
let cached: Promise<Taxonomy> | null = null;

export function loadTaxonomy(): Promise<Taxonomy> {
  if (!cached) {
    cached = getTaxonomy().catch((err) => {
      cached = null;
      throw err;
    });
  }
  return cached;
}

/** Tests start from an empty cache. */
export function resetTaxonomyCache() {
  cached = null;
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
