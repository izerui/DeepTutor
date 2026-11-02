"use client";

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { getScope, type PublishKind, type ScopeDescription, type ScopeUpdate } from "@/lib/publish-api";

import { isEmptyScope, ScopePicker, type ScopeValue } from "./ScopePicker";

/** What the publisher chose: ``scope`` is sent with the publication. */
export interface FirstPublishChoice {
  /** False when the item already has receivers (they stay as last set). */
  needed: boolean;
  /** Receivers to publish to, or ``undefined`` when none are needed. */
  scope?: ScopeUpdate;
  /** Whether the publication may go ahead. */
  ready: boolean;
}

export const toScopeUpdate = (value: ScopeValue): ScopeUpdate => ({
  everyone: value.everyone,
  users: value.people.map((p) => p.id),
  classes: value.classes,
});

/**
 * Who receives an item on its first publication (spec 2026-11-02 §4.1):
 * the classes the publisher teaches are preselected; someone who teaches
 * none chooses before publishing; "everyone" is only ever ticked on purpose.
 * An item that already has receivers keeps them.
 */
export function FirstPublishScope({
  kind,
  itemId,
  onChange,
}: {
  kind: PublishKind;
  itemId: string;
  onChange: (choice: FirstPublishChoice) => void;
}) {
  const { t } = useTranslation();
  const [described, setDescribed] = useState<ScopeDescription | null>(null);
  const [value, setValue] = useState<ScopeValue>({ everyone: false, classes: [], people: [] });
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    getScope(kind, itemId)
      .then((next) => {
        if (!alive) return;
        setDescribed(next);
        if (next.scope.managed) {
          onChange({ needed: false, ready: true });
          return;
        }
        const initial = { everyone: false, classes: next.suggested_classes, people: [] };
        setValue(initial);
        onChange({ needed: true, scope: toScopeUpdate(initial), ready: !isEmptyScope(initial) });
      })
      .catch((err) => {
        if (!alive) return;
        setError(err instanceof Error ? t(err.message) : String(err));
        onChange({ needed: true, ready: false });
      });
    return () => {
      alive = false;
    };
    // ``onChange`` is the caller's setter; the receivers load once per item.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, itemId, t]);

  if (error) return <p role="alert" className="text-xs text-red-600">{error}</p>;
  if (!described) return <p role="status" className="text-xs text-[var(--muted-foreground)]">{t("Loading…")}</p>;
  if (described.scope.managed) {
    return (
      <p className="text-xs text-[var(--muted-foreground)]">
        {t("It goes to the same receivers as last time ({{count}} people).", { count: described.audience_size })}
      </p>
    );
  }
  const change = (next: ScopeValue) => {
    setValue(next);
    onChange({ needed: true, scope: toScopeUpdate(next), ready: !isEmptyScope(next) });
  };
  return (
    <fieldset className="space-y-2 rounded-lg border border-[var(--border)] p-3" data-testid="first-publish-scope">
      <legend className="px-1 text-sm font-medium">{t("Who receives this")}</legend>
      <ScopePicker value={value} onChange={change} classes={described.choices.classes} />
      {isEmptyScope(value) && (
        <p className="text-xs text-amber-700 dark:text-amber-300">
          {t("Choose classes, people or everyone before publishing.")}
        </p>
      )}
    </fieldset>
  );
}
