"use client";

import { ChevronDown, ChevronRight, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { CATALOG_KINDS, type MarketFacets, type MarketQuery, type Taxonomy } from "@/lib/catalog-api";

import { kindLabel } from "./catalog-format";
import { clearConditions, removeValue, toggleValue, type FacetKey } from "./market-query";
import { pickName, subjectLabel, type TaxonomyIndexEntry } from "./useTaxonomy";

const TOP_TAGS = 20;
const TOP_AUTHORS = 10;

const optionClass = (on: boolean) =>
  `flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-[12.5px] transition-colors hover:bg-[var(--muted)] ${
    on ? "font-medium text-[var(--primary)]" : "text-[var(--foreground)]"
  }`;

function Option({ on, label, count, onToggle }: { on: boolean; label: string; count: number; onToggle: () => void }) {
  return (
    <button type="button" role="checkbox" aria-checked={on} className={optionClass(on)} onClick={onToggle}>
      <span aria-hidden className={`h-3 w-3 shrink-0 rounded-sm border ${on ? "border-[var(--primary)] bg-[var(--primary)]" : "border-[var(--border)]"}`} />
      <span className="min-w-0 flex-1 truncate">{label}</span>
      <span className="text-[11px] text-[var(--muted-foreground)]">{count}</span>
    </button>
  );
}

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <fieldset className="space-y-0.5">
      <legend className="mb-1 px-2 text-[11.5px] font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">{title}</legend>
      {children}
    </fieldset>
  );
}

/** Narrow a condition when a stage goes: subjects and grades of that stage go with it. */
export function toggleFacet(
  query: MarketQuery,
  key: FacetKey,
  value: string,
  index: Map<string, TaxonomyIndexEntry>,
): MarketQuery {
  const next = toggleValue(query, key, value);
  if (key === "stage" && (query.stage ?? []).includes(value)) {
    next.subject = (next.subject ?? []).filter((id) => index.get(id)?.stage !== value);
    next.grade = (next.grade ?? []).filter((id) => index.get(id)?.stage !== value);
  }
  return next;
}

/**
 * The market's conditions (§5.1): stage, subject (only under the chosen
 * stages, opening to the second level), kind, difficulty, the 20 most used
 * tags with a search, and authors with a search. Every option shows how
 * many results it would give with the other conditions applied.
 */
export function FacetSidebar({
  query,
  facets,
  taxonomy,
  index,
  onChange,
}: {
  query: MarketQuery;
  facets: MarketFacets | null;
  taxonomy: Taxonomy | null;
  index: Map<string, TaxonomyIndexEntry>;
  onChange: (next: MarketQuery) => void;
}) {
  const { t, i18n } = useTranslation();
  const lang = i18n?.language;
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [tagSearch, setTagSearch] = useState("");
  const [authorSearch, setAuthorSearch] = useState("");
  const toggle = (key: FacetKey, value: string) => onChange(toggleFacet(query, key, value, index));
  const has = (key: FacetKey, value: string) => (query[key] ?? []).includes(value);
  const count = (bucket: Record<string, number> | undefined, id: string) => bucket?.[id] ?? 0;

  const stages = taxonomy?.stages ?? [];
  const chosenStages = query.stage ?? [];
  const subjectStages = chosenStages.length ? stages.filter((s) => chosenStages.includes(s.id)) : stages;
  const subjects = subjectStages.flatMap((s) => s.subjects).filter(
    (s) => has("subject", s.id) || count(facets?.subjects, s.id) > 0 || s.children.some((c) => has("subject", c.id)),
  );
  // Follow-ups §7: outside one stage a subject says which stage it is under.
  const subjectName = (id: string) => subjectLabel(id, index, lang, chosenStages.length !== 1);
  // Follow-ups §2: grades only once a stage that has them is chosen (or a grade already is).
  const allGrades = taxonomy?.grades ?? [];
  const grades = allGrades.filter(
    (g) => has("grade", g.id) || (chosenStages.includes(g.stage) && count(facets?.grades, g.id) > 0),
  );
  const showGrades = (query.grade ?? []).length > 0 || allGrades.some((g) => chosenStages.includes(g.stage));

  const allTags = facets?.tags ?? [];
  const tagNeedle = tagSearch.trim().toLocaleLowerCase();
  const tags = (tagNeedle ? allTags.filter((x) => x.tag.toLocaleLowerCase().includes(tagNeedle)) : allTags.slice(0, TOP_TAGS));
  const chosenTags = (query.tag ?? []).filter((tag) => !tags.some((x) => x.tag.toLocaleLowerCase() === tag.toLocaleLowerCase()));

  const allAuthors = facets?.authors ?? [];
  const authorNeedle = authorSearch.trim().toLocaleLowerCase();
  const authorName = (a: { name: string; role: string }) => (a.role === "admin" || !a.name ? t("Platform") : a.name);
  const authors = authorNeedle
    ? allAuthors.filter((a) => authorName(a).toLocaleLowerCase().includes(authorNeedle))
    : allAuthors.slice(0, TOP_AUTHORS);

  return (
    <aside aria-label={t("Filters")} className="space-y-4" data-testid="facet-sidebar">
      <Group title={t("Stage")}>
        {stages
          .filter((s) => has("stage", s.id) || count(facets?.stages, s.id) > 0)
          .map((s) => (
            <Option key={s.id} on={has("stage", s.id)} label={pickName(s.names, lang)} count={count(facets?.stages, s.id)}
              onToggle={() => toggle("stage", s.id)} />
          ))}
      </Group>

      {subjects.length > 0 && (
        <Group title={t("Subject")}>
          {subjects.map((s) => {
            const children = s.children.filter((c) => has("subject", c.id) || count(facets?.subjects, c.id) > 0);
            const expanded = open[s.id] || children.some((c) => has("subject", c.id));
            return (
              <div key={s.id}>
                <div className="flex items-center">
                  {children.length > 0 ? (
                    <button type="button" aria-expanded={expanded}
                      aria-label={t("More specific subjects of {{name}}", { name: subjectName(s.id) })}
                      className="rounded p-0.5 text-[var(--muted-foreground)] hover:bg-[var(--muted)]"
                      onClick={() => setOpen((cur) => ({ ...cur, [s.id]: !expanded }))}>
                      {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                    </button>
                  ) : (
                    <span className="w-[16px]" />
                  )}
                  <Option on={has("subject", s.id)} label={subjectName(s.id)} count={count(facets?.subjects, s.id)}
                    onToggle={() => toggle("subject", s.id)} />
                </div>
                {expanded && (
                  <div className="pl-5">
                    {children.map((c) => (
                      <Option key={c.id} on={has("subject", c.id)} label={pickName(c.names, lang)} count={count(facets?.subjects, c.id)}
                        onToggle={() => toggle("subject", c.id)} />
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </Group>
      )}

      {showGrades && grades.length > 0 && (
        <Group title={t("Grade")}>
          {grades.map((g) => (
            <Option key={g.id} on={has("grade", g.id)} label={pickName(g.names, lang)} count={count(facets?.grades, g.id)}
              onToggle={() => toggle("grade", g.id)} />
          ))}
        </Group>
      )}

      <Group title={t("Type")}>
        {CATALOG_KINDS.map((kind) => (
          <Option key={kind} on={has("kind", kind)} label={kindLabel(kind, t)} count={count(facets?.kinds, kind)}
            onToggle={() => toggle("kind", kind)} />
        ))}
      </Group>

      <Group title={t("Difficulty")}>
        {(taxonomy?.difficulties ?? []).map((d) => (
          <Option key={d.id} on={has("difficulty", d.id)} label={pickName(d.names, lang)} count={count(facets?.difficulties, d.id)}
            onToggle={() => toggle("difficulty", d.id)} />
        ))}
      </Group>

      {(allTags.length > 0 || (query.tag ?? []).length > 0) && (
        <Group title={t("Tags")}>
          <input className="mb-1 h-7 w-full rounded-md border border-[var(--border)] bg-[var(--background)] px-2 text-[12px] outline-none focus:border-[var(--ring)]"
            value={tagSearch} onChange={(e) => setTagSearch(e.target.value)} aria-label={t("Search tags")} placeholder={t("Search tags")} />
          {chosenTags.map((tag) => (
            <Option key={tag} on label={tag} count={0} onToggle={() => toggle("tag", tag)} />
          ))}
          {tags.map((x) => (
            <Option key={x.tag} on={(query.tag ?? []).some((v) => v.toLocaleLowerCase() === x.tag.toLocaleLowerCase())}
              label={x.tag} count={x.count} onToggle={() => {
                const chosen = (query.tag ?? []).find((v) => v.toLocaleLowerCase() === x.tag.toLocaleLowerCase());
                toggle("tag", chosen ?? x.tag);
              }} />
          ))}
        </Group>
      )}

      {(allAuthors.length > 0 || (query.author ?? []).length > 0) && (
        <Group title={t("Author")}>
          <input className="mb-1 h-7 w-full rounded-md border border-[var(--border)] bg-[var(--background)] px-2 text-[12px] outline-none focus:border-[var(--ring)]"
            value={authorSearch} onChange={(e) => setAuthorSearch(e.target.value)} aria-label={t("Search authors")} placeholder={t("Search authors")} />
          {authors.map((a) => (
            <Option key={a.id} on={has("author", a.id)} label={authorName(a)} count={a.count} onToggle={() => toggle("author", a.id)} />
          ))}
        </Group>
      )}
    </aside>
  );
}

/** The chosen conditions above the results, each removable, plus "clear all" (§5.1). */
export function ActiveFilters({
  query,
  nameOf,
  subjectNameOf = nameOf,
  authorNames,
  onChange,
}: {
  query: MarketQuery;
  nameOf: (id: string) => string;
  /** A subject's name, led by its stage when not exactly one stage is chosen. */
  subjectNameOf?: (id: string) => string;
  authorNames: Record<string, string>;
  onChange: (next: MarketQuery) => void;
}) {
  const { t } = useTranslation();
  const chips: { key: FacetKey | "q"; value: string; label: string }[] = [];
  if (query.q?.trim()) chips.push({ key: "q", value: query.q, label: `“${query.q.trim()}”` });
  for (const value of query.stage ?? []) chips.push({ key: "stage", value, label: nameOf(value) });
  for (const value of query.subject ?? []) chips.push({ key: "subject", value, label: subjectNameOf(value) });
  for (const value of query.grade ?? []) chips.push({ key: "grade", value, label: nameOf(value) });
  for (const value of query.kind ?? []) chips.push({ key: "kind", value, label: kindLabel(value, t) });
  for (const value of query.difficulty ?? []) chips.push({ key: "difficulty", value, label: nameOf(value) });
  for (const value of query.tag ?? []) chips.push({ key: "tag", value, label: `#${value}` });
  for (const value of query.author ?? []) chips.push({ key: "author", value, label: authorNames[value] || t("Platform") });
  if (chips.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5" data-testid="active-filters">
      {chips.map((chip) => (
        <span key={`${chip.key}:${chip.value}`} className="inline-flex items-center gap-1 rounded-full bg-[var(--muted)] px-2.5 py-0.5 text-[12px]">
          {chip.label}
          <button type="button" aria-label={t("Remove filter {{name}}", { name: chip.label })}
            className="text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
            onClick={() => onChange(chip.key === "q" ? { ...query, q: "", page: 1 } : removeValue(query, chip.key, chip.value))}>
            <X size={11} />
          </button>
        </span>
      ))}
      <button type="button" className="text-[12px] text-[var(--primary)] hover:underline" onClick={() => onChange(clearConditions(query))}>
        {t("Clear all")}
      </button>
    </div>
  );
}
