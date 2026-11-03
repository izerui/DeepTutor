"use client";

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { UserPlus, X } from "lucide-react";

import { searchPeople } from "@/lib/publish-api";

export type Person = { id: string; username: string };

export interface ScopeValue {
  everyone: boolean;
  classes: string[];
  people: Person[];
  /** Departments, grades or both: ``department:<id>``, ``grade:<id>``, joined by ``+``. */
  groups?: { id: string; name: string }[];
}

export const isEmptyScope = (value: ScopeValue) =>
  !value.everyone && value.classes.length === 0 && value.people.length === 0 && (value.groups ?? []).length === 0;

type OrgItem = { id: string; name: string };

/** Administrators pick a whole department, a whole grade, or a department's grade (2026-11-03 §0). */
function GroupPicker({
  value,
  onChange,
  departments,
  grades,
}: {
  value: ScopeValue;
  onChange: (next: ScopeValue) => void;
  departments: OrgItem[];
  grades: OrgItem[];
}) {
  const { t } = useTranslation();
  const [department, setDepartment] = useState("");
  const [grade, setGrade] = useState("");
  const groups = value.groups ?? [];
  const add = () => {
    const parts = [department && `department:${department}`, grade && `grade:${grade}`].filter(Boolean);
    if (!parts.length) return;
    const id = parts.join("+");
    if (groups.some((g) => g.id === id)) return;
    const name = [departments.find((d) => d.id === department)?.name, grades.find((g) => g.id === grade)?.name]
      .filter(Boolean)
      .join(" · ");
    onChange({ ...value, groups: [...groups, { id, name }] });
    setDepartment("");
    setGrade("");
  };
  return (
    <div className="space-y-1">
      <p className="text-[var(--muted-foreground)]">{t("Departments and grades (classes added later are included)")}</p>
      {groups.length > 0 && (
        <ul className="flex flex-wrap gap-1">
          {groups.map((g) => (
            <li key={g.id} className="inline-flex items-center gap-1 rounded-full bg-[var(--muted)] px-2 py-0.5">
              {g.name || g.id}
              <button type="button" aria-label={t("Remove {{name}}", { name: g.name })}
                onClick={() => onChange({ ...value, groups: groups.filter((x) => x.id !== g.id) })}>
                <X className="h-3 w-3" />
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap items-center gap-1">
        <select aria-label={t("Department")} value={department} onChange={(e) => setDepartment(e.target.value)}
          className="rounded-md border border-[var(--border)] bg-[var(--background)] px-1.5 py-1">
          <option value="">{t("Any department")}</option>
          {departments.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
        </select>
        <select aria-label={t("Grade")} value={grade} onChange={(e) => setGrade(e.target.value)}
          className="rounded-md border border-[var(--border)] bg-[var(--background)] px-1.5 py-1">
          <option value="">{t("Any grade")}</option>
          {grades.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
        </select>
        <button type="button" disabled={!department && !grade} onClick={add}
          className="rounded-md border border-[var(--border)] px-2 py-1 disabled:opacity-50">
          {t("Add")}
        </button>
      </div>
    </div>
  );
}

/**
 * The receivers of a published item: everyone, classes (later joiners
 * included) and specific people. Controlled; saving is the caller's.
 */
export function ScopePicker({
  value,
  onChange,
  classes,
  departments = [],
  grades = [],
}: {
  value: ScopeValue;
  onChange: (next: ScopeValue) => void;
  classes: { id: string; name: string }[];
  /** Only passed for administrators. */
  departments?: OrgItem[];
  grades?: OrgItem[];
}) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Person[]>([]);

  useEffect(() => {
    const needle = query.trim();
    if (!needle) return;
    let alive = true;
    const timer = window.setTimeout(() => {
      void searchPeople(needle)
        .then((rows) => alive && setResults(rows))
        .catch(() => alive && setResults([]));
    }, 250);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [query]);

  const toggleClass = (id: string) =>
    onChange({
      ...value,
      classes: value.classes.includes(id) ? value.classes.filter((c) => c !== id) : [...value.classes, id],
    });

  return (
    <div className="space-y-2 text-xs">
      <label className="flex items-center gap-2">
        <input
          type="checkbox"
          checked={value.everyone}
          onChange={(e) => onChange({ ...value, everyone: e.target.checked })}
        />
        {t("Everyone")}
      </label>
      {classes.length > 0 && (
        <div className="space-y-1">
          <p className="text-[var(--muted-foreground)]">{t("Classes (students who join later are included)")}</p>
          {classes.map((c) => (
            <label key={c.id} className="flex items-center gap-2">
              <input type="checkbox" checked={value.classes.includes(c.id)} onChange={() => toggleClass(c.id)} />
              {c.name || c.id}
            </label>
          ))}
        </div>
      )}
      {(departments.length > 0 || grades.length > 0) && (
        <GroupPicker value={value} onChange={onChange} departments={departments} grades={grades} />
      )}
      <div className="space-y-1">
        <p className="text-[var(--muted-foreground)]">{t("Specific people")}</p>
        {value.people.length > 0 && (
          <ul className="flex flex-wrap gap-1">
            {value.people.map((p) => (
              <li key={p.id} className="inline-flex items-center gap-1 rounded-full bg-[var(--muted)] px-2 py-0.5">
                {p.username}
                <button
                  type="button"
                  aria-label={t("Remove {{name}}", { name: p.username })}
                  onClick={() => onChange({ ...value, people: value.people.filter((x) => x.id !== p.id) })}
                >
                  <X className="h-3 w-3" />
                </button>
              </li>
            ))}
          </ul>
        )}
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("Search by username")}
          className="w-full rounded-md border border-[var(--border)] bg-[var(--background)] px-2 py-1"
        />
        {query.trim() && results.length > 0 && (
          <ul className="max-h-36 space-y-0.5 overflow-y-auto">
            {results
              .filter((r) => !value.people.some((p) => p.id === r.id))
              .map((r) => (
                <li key={r.id}>
                  <button
                    type="button"
                    className="inline-flex items-center gap-1 hover:underline"
                    onClick={() => {
                      onChange({ ...value, people: [...value.people, { id: r.id, username: r.username }] });
                      setQuery("");
                    }}
                  >
                    <UserPlus className="h-3 w-3" />
                    {r.username}
                  </button>
                </li>
              ))}
          </ul>
        )}
      </div>
    </div>
  );
}
