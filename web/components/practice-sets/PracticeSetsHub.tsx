"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ClipboardList, Plus } from "lucide-react";

import { actionClass, inputClass, primaryClass } from "@/components/classes/ClassesHub";
import { useAuthStatus } from "@/hooks/useAuthStatus";
import {
  createSet,
  listAssigned,
  listMySets,
  type AssignedSet,
  type PracticeSetSummary,
} from "@/lib/practice-sets-api";
import { SetEditor } from "./SetEditor";
import { TakeSet } from "./TakeSet";

export const formatDate = (timestamp: number | null | undefined) =>
  timestamp ? new Date(timestamp * 1000).toLocaleString() : "";

/**
 * Practice sets (spec 2026-10-29 §8): teachers and administrators write and
 * publish them; students answer the ones published to them.
 */
export default function PracticeSetsHub() {
  const { t } = useTranslation();
  const { isAdmin, isTeacher, loading } = useAuthStatus();

  if (loading) return <div aria-busy="true" className="h-40 animate-pulse rounded-xl bg-[var(--muted)]" />;
  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-serif text-[27px] text-[var(--foreground)]">{t("Practice sets")}</h1>
        <p className="mt-1 text-[13px] text-[var(--muted-foreground)]">
          {isAdmin || isTeacher
            ? t("Write question sets, publish them to classes or people, and see how students did.")
            : t("Question sets your teachers published to you. Your answers go to your own question bank.")}
        </p>
      </header>
      {isAdmin || isTeacher ? <AuthorView /> : <StudentView />}
    </div>
  );
}

const statusLabel: Record<PracticeSetSummary["status"], string> = {
  draft: "Draft",
  published: "Published",
  archived: "Archived",
};

function AuthorView() {
  const { t } = useTranslation();
  const [sets, setSets] = useState<PracticeSetSummary[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [error, setError] = useState("");

  const refresh = useCallback(() => {
    listMySets()
      .then(setSets)
      .catch((err) => setError(err instanceof Error ? t(err.message) : String(err)));
  }, [t]);
  useEffect(refresh, [refresh]);

  const create = async () => {
    try {
      const created = await createSet(title.trim());
      setTitle("");
      setSelected(created.id);
      refresh();
    } catch (err) {
      setError(err instanceof Error ? t(err.message) : String(err));
    }
  };

  return (
    <div className="grid gap-5 lg:grid-cols-[280px_1fr]">
      <aside className="space-y-3">
        <form
          className="flex gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (title.trim()) void create();
          }}
        >
          <input
            className={inputClass}
            value={title}
            maxLength={200}
            onChange={(e) => setTitle(e.target.value)}
            placeholder={t("New practice set title")}
          />
          <button type="submit" className={primaryClass} disabled={!title.trim()} aria-label={t("Create")}>
            <Plus size={14} />
          </button>
        </form>
        {error && <p role="alert" className="text-[12px] text-[var(--destructive)]">{error}</p>}
        {sets === null ? (
          <div aria-busy="true" className="h-24 animate-pulse rounded-xl bg-[var(--muted)]" />
        ) : sets.length === 0 ? (
          <p className="text-[12.5px] text-[var(--muted-foreground)]">{t("No practice sets yet.")}</p>
        ) : (
          <ul className="space-y-1">
            {sets.map((set) => (
              <li key={set.id}>
                <button
                  type="button"
                  onClick={() => setSelected(set.id)}
                  className={`w-full rounded-lg px-3 py-2 text-left text-[13px] hover:bg-[var(--muted)] ${
                    selected === set.id ? "bg-[var(--muted)]" : ""
                  }`}
                >
                  <span className="block truncate font-medium text-[var(--foreground)]">{set.title}</span>
                  <span className="text-[11.5px] text-[var(--muted-foreground)]">
                    {t(statusLabel[set.status])}
                    {set.current_revision > 0 && ` · v${set.current_revision}`}
                    {set.submitted_students ? ` · ${t("{{count}} submitted", { count: set.submitted_students })}` : ""}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </aside>
      <section className="min-w-0">
        {selected ? (
          <SetEditor key={selected} setId={selected} onChanged={refresh} onDeleted={() => { setSelected(null); refresh(); }} />
        ) : (
          <div className="flex h-40 items-center justify-center rounded-xl border border-dashed border-[var(--border)] text-[13px] text-[var(--muted-foreground)]">
            <ClipboardList size={16} className="mr-2" />
            {t("Pick a practice set or create one.")}
          </div>
        )}
      </section>
    </div>
  );
}

function StudentView() {
  const { t } = useTranslation();
  const [sets, setSets] = useState<AssignedSet[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState("");

  const refresh = useCallback(() => {
    listAssigned()
      .then(setSets)
      .catch((err) => setError(err instanceof Error ? t(err.message) : String(err)));
  }, [t]);
  useEffect(refresh, [refresh]);

  if (selected) {
    return (
      <div className="space-y-3">
        <button type="button" className={actionClass} onClick={() => { setSelected(null); refresh(); }}>
          {t("Back to practice sets")}
        </button>
        <TakeSet setId={selected} />
      </div>
    );
  }
  if (error) return <p role="alert" className="text-[12px] text-[var(--destructive)]">{error}</p>;
  if (sets === null) return <div aria-busy="true" className="h-24 animate-pulse rounded-xl bg-[var(--muted)]" />;
  if (sets.length === 0) {
    return <p className="text-[13px] text-[var(--muted-foreground)]">{t("No practice sets for you yet.")}</p>;
  }
  return (
    <ul className="grid gap-3 sm:grid-cols-2">
      {sets.map((set) => (
        <li key={set.id} className="space-y-1.5 rounded-xl border border-[var(--border)] p-4">
          <p className="text-[14px] font-medium text-[var(--foreground)]">{set.title}</p>
          <p className="text-[12px] text-[var(--muted-foreground)]">
            {set.teacher}
            {set.due_at ? ` · ${t("Due {{date}}", { date: formatDate(set.due_at) })}` : ""}
          </p>
          <p className="text-[12px] text-[var(--muted-foreground)]">
            {set.submissions > 0
              ? t("Best score {{score}} / {{max}}", { score: set.best_score, max: set.max_score })
              : t("Not answered yet")}
          </p>
          <button type="button" className={primaryClass} onClick={() => setSelected(set.id)}>
            {set.submissions > 0 ? t("Answer again") : t("Start")}
          </button>
        </li>
      ))}
    </ul>
  );
}
