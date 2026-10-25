"use client";

import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertCircle, BookOpen, CheckCircle2, Loader2, Save } from "lucide-react";
import { fetchBookPermission, saveBookPermission } from "../api";
import type { BookPermission } from "../types";

export function BookPermissionEditor({ userId }: { userId: string }) {
  // A different account must never inherit pending edits or request feedback.
  return <UserBookPermissionEditor key={userId} userId={userId} />;
}

function UserBookPermissionEditor({ userId }: { userId: string }) {
  const { t } = useTranslation();
  const inputId = useId();
  const active = useRef(false);
  const [permission, setPermission] = useState<BookPermission>({ create: true });
  const [saved, setSaved] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [savedNow, setSavedNow] = useState(false);

  useEffect(() => {
    let cancelled = false;
    active.current = true;
    fetchBookPermission(userId)
      .then((next) => {
        if (cancelled) return;
        const value = { create: next.create !== false };
        setPermission(value);
        setSaved(value.create);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
      active.current = false;
    };
  }, [userId]);

  const dirty = saved !== null && saved !== permission.create;
  const save = async () => {
    if (!active.current || !dirty || saving) return;
    setSaving(true);
    setError("");
    setSavedNow(false);
    try {
      const next = await saveBookPermission(userId, { create: permission.create });
      if (!active.current) return;
      const value = { create: next.create !== false };
      setPermission(value);
      setSaved(value.create);
      setSavedNow(true);
    } catch (err) {
      if (active.current) setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (active.current) setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center gap-2 border-t border-[var(--border)] px-5 py-4 text-xs text-[var(--muted-foreground)]">
        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> {t("Loading book permissions…")}
      </div>
    );
  }

  return (
    <section className="border-t border-[var(--border)] bg-[var(--background)]/40 px-5 py-4">
      <div className="mb-3 flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-[var(--foreground)]">
            <BookOpen className="h-4 w-4" aria-hidden="true" /> {t("Book access")}
          </h3>
          <p className="mt-1 text-xs text-[var(--muted-foreground)]">
            {t("Per-book access has been retired. Books are shared by making them public; edits stay with the creator and admins.")}
          </p>
        </div>
        <button
          type="button"
          onClick={() => void save()}
          disabled={!dirty || saving}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-md bg-[var(--primary)] px-3 py-1.5 text-xs font-medium text-[var(--primary-foreground)] disabled:opacity-40"
        >
          {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : <Save className="h-3.5 w-3.5" aria-hidden="true" />}
          {t("Save")}
        </button>
      </div>
      <label htmlFor={inputId} className="flex items-center gap-2 rounded-lg border border-[var(--border)] p-3 text-xs">
        <input
          id={inputId}
          type="checkbox"
          checked={permission.create}
          disabled={saving || saved === null}
          onChange={(event) => {
            setSavedNow(false);
            setPermission({ create: event.target.checked });
          }}
        />
        {t("May create personal books")}
      </label>
      {error ? (
        <p role="alert" className="mt-3 flex items-center gap-1.5 text-xs text-red-600">
          <AlertCircle className="h-3.5 w-3.5" aria-hidden="true" /> {error}
        </p>
      ) : savedNow ? (
        <p role="status" className="mt-3 flex items-center gap-1.5 text-xs text-emerald-600">
          <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" /> {t("Saved")}
        </p>
      ) : dirty ? (
        <p className="mt-3 text-xs text-amber-600">{t("Unsaved book permission changes")}</p>
      ) : null}
    </section>
  );
}
