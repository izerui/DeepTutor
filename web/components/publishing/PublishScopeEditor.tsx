"use client";

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  getScope,
  PublishApiError,
  setScope,
  type PublishKind,
  type ScopeDescription,
} from "@/lib/publish-api";

import { ScopePicker, type Person } from "./ScopePicker";

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

  // A confirmation covers the scope that was shown; any later edit asks again.
  useEffect(() => setAffected(null), [everyone, classes, people]);

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
  const knownClasses = [
    ...described.choices.classes,
    ...described.scope.classes.filter((c) => !described.choices.classes.some((choice) => choice.id === c.id)),
  ];
  const nothing = !everyone && classes.length === 0 && people.length === 0;

  return (
    <fieldset disabled={busy} className="space-y-2 text-xs" data-testid="publish-scope-editor">
      <legend className="mb-1 text-sm font-medium">{t("Who receives this")}</legend>
      <ScopePicker
        value={{ everyone, classes, people }}
        classes={knownClasses}
        onChange={(next) => {
          setEveryone(next.everyone);
          setClasses(next.classes);
          setPeople(next.people);
        }}
      />
      {!described.scope.managed && described.published && kind !== "practice_set" && (
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
