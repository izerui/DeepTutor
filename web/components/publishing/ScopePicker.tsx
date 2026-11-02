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
}

export const isEmptyScope = (value: ScopeValue) =>
  !value.everyone && value.classes.length === 0 && value.people.length === 0;

/**
 * The receivers of a published item: everyone, classes (later joiners
 * included) and specific people. Controlled; saving is the caller's.
 */
export function ScopePicker({
  value,
  onChange,
  classes,
}: {
  value: ScopeValue;
  onChange: (next: ScopeValue) => void;
  classes: { id: string; name: string }[];
}) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Person[]>([]);

  useEffect(() => {
    const needle = query.trim();
    if (!needle) {
      setResults([]);
      return;
    }
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
        {results.length > 0 && (
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
