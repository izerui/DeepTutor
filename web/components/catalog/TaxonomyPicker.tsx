"use client";

import { ChevronDown, ChevronRight } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import type { Taxonomy } from "@/lib/catalog-api";

import { indexTaxonomy, loadFullTaxonomy, pickName, type TaxonomyIndexEntry } from "./useTaxonomy";

export const MAX_STAGES = 3;
export const MAX_SUBJECTS = 3;

export interface Classification {
  stages: string[];
  subjects: string[];
  difficulty: string;
}

const chip = (on: boolean) =>
  `inline-flex items-center rounded-full border px-2.5 py-1 text-[12px] transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
    on
      ? "border-[var(--primary)] bg-[var(--primary)]/10 text-[var(--primary)]"
      : "border-[var(--border)] text-[var(--foreground)] hover:bg-[var(--muted)]"
  }`;

/** Stages, then subjects under the chosen stages (first or second level), then a difficulty (§3). */
export function TaxonomyPicker({
  taxonomy,
  value,
  onChange,
  disabled = false,
}: {
  taxonomy: Taxonomy;
  value: Classification;
  onChange: (next: Classification) => void;
  disabled?: boolean;
}) {
  const { t, i18n } = useTranslation();
  const lang = i18n?.language;
  const [open, setOpen] = useState<Record<string, boolean>>({});

  const toggleStage = (id: string) => {
    if (value.stages.includes(id)) {
      const stage = taxonomy.stages.find((s) => s.id === id);
      const inStage = new Set(stage?.subjects.flatMap((s) => [s.id, ...s.children.map((c) => c.id)]) ?? []);
      onChange({ ...value, stages: value.stages.filter((s) => s !== id), subjects: value.subjects.filter((s) => !inStage.has(s)) });
    } else if (value.stages.length < MAX_STAGES) {
      onChange({ ...value, stages: [...value.stages, id] });
    }
  };
  const toggleSubject = (id: string) => {
    if (value.subjects.includes(id)) onChange({ ...value, subjects: value.subjects.filter((s) => s !== id) });
    else if (value.subjects.length < MAX_SUBJECTS) onChange({ ...value, subjects: [...value.subjects, id] });
  };
  const subjectsFull = value.subjects.length >= MAX_SUBJECTS;

  // Platform batch 2 §8.1: an entry an administrator hid stays on what already uses
  // it. The public classification omits it, so it is kept and shown here by name
  // (administrators) or by its id, never dropped silently on save.
  const known = useMemo(() => indexTaxonomy(taxonomy), [taxonomy]);
  const kept = [...value.stages, ...value.subjects].filter((id) => !known.has(id));
  const [fullIndex, setFullIndex] = useState<Map<string, TaxonomyIndexEntry> | null>(null);
  const needFull = kept.length > 0;
  useEffect(() => {
    if (!needFull) return;
    let alive = true;
    loadFullTaxonomy()
      .then((full) => alive && setFullIndex(indexTaxonomy(full)))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [needFull]);
  const keptName = (id: string) => {
    const found = fullIndex?.get(id);
    return found ? pickName(found.names, lang) || id : id;
  };
  const dropKept = (id: string) =>
    onChange({ ...value, stages: value.stages.filter((s) => s !== id), subjects: value.subjects.filter((s) => s !== id) });

  return (
    <div className="space-y-3">
      <fieldset className="space-y-1.5" disabled={disabled}>
        <legend className="text-[12.5px] font-medium">
          {t("Stage")} <span className="font-normal text-[var(--muted-foreground)]">{t("(1 to 3)")}</span>
        </legend>
        <div className="flex flex-wrap gap-1.5">
          {taxonomy.stages.map((stage) => {
            const on = value.stages.includes(stage.id);
            return (
              <button key={stage.id} type="button" aria-pressed={on} className={chip(on)}
                disabled={!on && value.stages.length >= MAX_STAGES} onClick={() => toggleStage(stage.id)}>
                {pickName(stage.names, lang)}
              </button>
            );
          })}
        </div>
      </fieldset>

      <fieldset className="space-y-1.5" disabled={disabled}>
        <legend className="text-[12.5px] font-medium">
          {t("Subject")} <span className="font-normal text-[var(--muted-foreground)]">{t("(1 to 3)")}</span>
        </legend>
        {value.stages.length === 0 ? (
          <p className="text-[11.5px] text-[var(--muted-foreground)]">{t("Choose a stage first.")}</p>
        ) : (
          taxonomy.stages
            .filter((stage) => value.stages.includes(stage.id))
            .map((stage) => (
              <div key={stage.id} className="space-y-1">
                <p className="text-[11.5px] text-[var(--muted-foreground)]">{pickName(stage.names, lang)}</p>
                <ul className="space-y-1">
                  {stage.subjects.map((subject) => {
                    const on = value.subjects.includes(subject.id);
                    const expanded = open[subject.id] || subject.children.some((c) => value.subjects.includes(c.id));
                    return (
                      <li key={subject.id} className="flex flex-wrap items-center gap-1.5">
                        {subject.children.length > 0 && (
                          <button type="button" className="rounded p-0.5 text-[var(--muted-foreground)] hover:bg-[var(--muted)]"
                            aria-expanded={expanded}
                            aria-label={t("More specific subjects of {{name}}", { name: pickName(subject.names, lang) })}
                            onClick={() => setOpen((cur) => ({ ...cur, [subject.id]: !expanded }))}>
                            {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                          </button>
                        )}
                        <button type="button" aria-pressed={on} className={chip(on)} disabled={!on && subjectsFull}
                          onClick={() => toggleSubject(subject.id)}>
                          {pickName(subject.names, lang)}
                        </button>
                        {expanded &&
                          subject.children.map((child) => {
                            const childOn = value.subjects.includes(child.id);
                            return (
                              <button key={child.id} type="button" aria-pressed={childOn} className={chip(childOn)}
                                disabled={!childOn && subjectsFull} onClick={() => toggleSubject(child.id)}>
                                {pickName(child.names, lang)}
                              </button>
                            );
                          })}
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))
        )}
      </fieldset>

      {kept.length > 0 && (
        <fieldset className="space-y-1.5" disabled={disabled} data-testid="taxonomy-kept">
          <legend className="text-[12.5px] font-medium">{t("No longer offered")}</legend>
          <p className="text-[11.5px] text-[var(--muted-foreground)]">
            {t("These stay as they are; they cannot be chosen again once removed.")}
          </p>
          <div className="flex flex-wrap gap-1.5">
            {kept.map((id) => (
              <button key={id} type="button" aria-pressed className={chip(true)} onClick={() => dropKept(id)}>
                {keptName(id)}
              </button>
            ))}
          </div>
        </fieldset>
      )}

      <label className="block space-y-1">
        <span className="text-[12.5px] font-medium">{t("Difficulty")}</span>
        <select
          className="h-9 w-full max-w-xs rounded-lg border border-[var(--border)] bg-[var(--background)] px-2 text-[13px]"
          value={value.difficulty}
          disabled={disabled}
          onChange={(e) => onChange({ ...value, difficulty: e.target.value })}
        >
          <option value="">{t("Choose a difficulty")}</option>
          {taxonomy.difficulties.map((d) => (
            <option key={d.id} value={d.id}>{pickName(d.names, lang)}</option>
          ))}
        </select>
      </label>
    </div>
  );
}
