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
  kind: "stage" | "subject" | "grade" | "difficulty";
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
  for (const grade of taxonomy.grades ?? []) index.set(grade.id, { kind: "grade", names: grade.names, stage: grade.stage });
  for (const difficulty of taxonomy.difficulties ?? []) index.set(difficulty.id, { kind: "difficulty", names: difficulty.names });
  return index;
}

const NAME_LANGUAGES = ["zh", "en", "de", "fr", "pl", "uk"] as const;

/**
 * The name in the interface language (follow-ups §7): the built-in entries have
 * German, French, Polish and Ukrainian names too; otherwise English (Chinese for
 * a Chinese interface).
 */
export function pickName(names: TaxonomyName | undefined, language: string | undefined): string {
  if (!names) return "";
  const code = NAME_LANGUAGES.find((lang) => language?.toLowerCase().startsWith(lang));
  if (code === "zh") return names.zh || names.en;
  return (code && names[code]) || names.en || names.zh;
}

/**
 * A subject's name, led by its stage (and first-level subject) when it is shown
 * outside the context of one stage (follow-ups §7): "Primary school · Mathematics".
 */
export function subjectLabel(
  id: string,
  index: Map<string, TaxonomyIndexEntry>,
  language: string | undefined,
  withStage: boolean,
): string {
  const found = index.get(id);
  if (!found) return id;
  const own = pickName(found.names, language);
  if (!withStage || found.kind !== "subject") return own;
  const parts = [found.stage ? pickName(index.get(found.stage)?.names, language) : "", found.parent ? pickName(index.get(found.parent)?.names, language) : "", own];
  return parts.filter(Boolean).join(" · ");
}

/**
 * One line for an item's classification: per stage, its grades and subjects
 * ("Primary school · Grade 3 · Mathematics"); several stages are separated by " / ",
 * so every subject stays next to its stage.
 */
export function classificationLabel(
  entry: { stages?: string[]; subjects?: string[]; grades?: string[] },
  index: Map<string, TaxonomyIndexEntry>,
  language: string | undefined,
): string {
  const stages = entry.stages ?? [];
  const name = (id: string) => {
    const found = index.get(id);
    return found ? pickName(found.names, language) : id;
  };
  const used = new Set<string>();
  const groups = stages.map((stage) => {
    const under = [...(entry.grades ?? []), ...(entry.subjects ?? [])].filter((id) => index.get(id)?.stage === stage);
    under.forEach((id) => used.add(id));
    return [name(stage), ...under.map(name)].join(" · ");
  });
  // Anything not under one of its stages (unknown, or hidden later) keeps its own stage in front.
  for (const id of [...(entry.grades ?? []), ...(entry.subjects ?? [])]) {
    if (!used.has(id)) groups.push(subjectLabel(id, index, language, true));
  }
  return groups.join(" / ");
}

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
  const classificationOf = useMemo(
    () => (entry: { stages?: string[]; subjects?: string[]; grades?: string[] }) => classificationLabel(entry, index, language),
    [index, language],
  );
  return { taxonomy, index, nameOf, classificationOf, error, language };
}
