"use client";

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { UserPlus, X } from "lucide-react";

import {
  getScope,
  PublishApiError,
  searchPeople,
  setScope,
  type PublishKind,
  type ScopeDescription,
} from "@/lib/publish-api";

type Person = { id: string; username: string };

/**
 * Who receives a published item (spec 2026-10-29 §7): specific people,
 * classes (later joiners included) and/or everyone. Narrowing asks first.
 */
export function PublishScopeEditor({
  kind,
  itemId,
  onSaved,
}: {
  kind: PublishKind;
  itemId: string;
  onSaved?: () => void;
}) {
  const { t } = useTranslation();
  const [described, setDescribed] = useState<ScopeDescription | null>(null);
  const [everyone, setEveryone] = useState(false);
  const [classes, setClasses] = useState<string[]>([]);
  const [people, setPeople] = useState<Person[]>([]);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Person[]>([]);
  const [affected, setAffected] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  // Only the publisher manages the scope; anyone else sees nothing here.
  const [hidden, setHidden] = useState(false);

  const load = (next: ScopeDescription) => {
    setDescribed(next);
    setEveryone(next.scope.everyone);
    setClasses(next.scope.classes.map((c) => c.id));
    setPeople(next.scope.users);
    setAffected(null);
  };

  useEffect(() => {
    let alive = true;
    getScope(kind, itemId)
      .then((next) => alive && load(next))
      .catch((err) => {
        if (!alive) return;
        if (err instanceof PublishApiError && (err.status === 403 || err.status === 404)) setHidden(true);
        else setError(err instanceof Error ? t(err.message) : String(err));
      });
    return () => {
      alive = false;
    };
  }, [kind, itemId, t]);

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

  const save = async (confirm: boolean) => {
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      load(await setScope(kind, itemId, { everyone, users: people.map((p) => p.id), classes, confirm }));
      setSaved(true);
      onSaved?.();
    } catch (err) {
      if (err instanceof PublishApiError && err.affected !== null) setAffected(err.affected);
      else setError(err instanceof Error ? t(err.message) : String(err));
    } finally {
      setBusy(false);
    }
  };

  if (hidden) return null;
  if (!described) {
    // A load failure only hides this optional panel's controls; it never blocks the dialog around it.
    return error ? (
      <p className="text-xs text-[var(--muted-foreground)]">{error}</p>
    ) : (
      <p role="status" className="text-xs text-[var(--muted-foreground)]">{t("Loading…")}</p>
    );
  }
  const toggleClass = (id: string) =>
    setClasses((current) => (current.includes(id) ? current.filter((c) => c !== id) : [...current, id]));
  const knownClasses = [
    ...described.choices.classes,
    ...described.scope.classes.filter((c) => !described.choices.classes.some((choice) => choice.id === c.id)),
  ];
  const nothing = !everyone && classes.length === 0 && people.length === 0;

  return (
    <fieldset disabled={busy} className="space-y-2 text-xs" data-testid="publish-scope-editor">
      <legend className="mb-1 text-sm font-medium">{t("Who receives this")}</legend>
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={everyone} onChange={(e) => setEveryone(e.target.checked)} />
        {t("Everyone")}
      </label>
      {knownClasses.length > 0 && (
        <div className="space-y-1">
          <p className="text-[var(--muted-foreground)]">{t("Classes (students who join later are included)")}</p>
          {knownClasses.map((c) => (
            <label key={c.id} className="flex items-center gap-2">
              <input type="checkbox" checked={classes.includes(c.id)} onChange={() => toggleClass(c.id)} />
              {c.name || c.id}
            </label>
          ))}
        </div>
      )}
      <div className="space-y-1">
        <p className="text-[var(--muted-foreground)]">{t("Specific people")}</p>
        {people.length > 0 && (
          <ul className="flex flex-wrap gap-1">
            {people.map((p) => (
              <li key={p.id} className="inline-flex items-center gap-1 rounded-full bg-[var(--muted)] px-2 py-0.5">
                {p.username}
                <button
                  type="button"
                  aria-label={t("Remove {{name}}", { name: p.username })}
                  onClick={() => setPeople((current) => current.filter((x) => x.id !== p.id))}
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
              .filter((r) => !people.some((p) => p.id === r.id))
              .map((r) => (
                <li key={r.id}>
                  <button
                    type="button"
                    className="inline-flex items-center gap-1 hover:underline"
                    onClick={() => {
                      setPeople((current) => [...current, { id: r.id, username: r.username }]);
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
      {!described.scope.managed && described.published && (
        <p className="text-[var(--muted-foreground)]">
          {kind === "mastery_path"
            ? t("Currently only the students you picked receive this path.")
            : t("Currently every signed-in account can read this.")}
        </p>
      )}
      {nothing && <p className="text-amber-700 dark:text-amber-300">{t("Nobody but you will receive this.")}</p>}
      {affected !== null ? (
        <div role="alert" className="space-y-1.5 text-amber-700 dark:text-amber-300">
          <p>{t("This change removes access for {{count}} people. Their own records are kept.", { count: affected })}</p>
          <div className="flex gap-2">
            <button type="button" onClick={() => void save(true)} className="rounded-md bg-[var(--primary)] px-2.5 py-1 text-[var(--primary-foreground)]">
              {t("Save anyway")}
            </button>
            <button type="button" onClick={() => setAffected(null)} className="rounded-md border border-[var(--border)] px-2.5 py-1">
              {t("Cancel")}
            </button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => void save(false)}
          className="rounded-md border border-[var(--border)] px-2.5 py-1"
        >
          {t("Save who receives this")}
        </button>
      )}
      {saved && <p role="status" className="text-[var(--muted-foreground)]">{t("Saved. {{count}} people receive this.", { count: described.audience_size })}</p>}
      {error && <p role="alert" className="text-red-600">{error}</p>}
    </fieldset>
  );
}
