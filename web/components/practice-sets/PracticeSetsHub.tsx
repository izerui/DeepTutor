"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ClipboardList, Plus } from "lucide-react";

import { actionClass, inputClass, primaryClass } from "@/components/common/form-styles";
import { SourceBadge } from "@/components/common/SourceBadge";
import { useAuthStatus } from "@/hooks/useAuthStatus";
import {
  archiveSet,
  createSet,
  listAssigned,
  listMySets,
  listWithdrawable,
  type AssignedSet,
  type PracticeSetSummary,
} from "@/lib/practice-sets-api";
import { SetEditor } from "./SetEditor";
import { TakeSet } from "./TakeSet";

import { formatDate } from "./practice-format";

/**
 * Practice sets (spec 2026-10-29 §8): teachers and administrators write and
 * publish them; everyone answers the ones they joined or were sent by name.
 */
export default function PracticeSetsHub() {
  const { t } = useTranslation();
  const { isAdmin, isTeacher, loading } = useAuthStatus();
  // ``?set=`` opens one directly (a notification, the market's "Go study", a report).
  const params = useSearchParams();
  const setParam = params?.get("set") ?? null;

  if (loading) return <div aria-busy="true" className="h-40 animate-pulse rounded-xl bg-[var(--muted)]" />;
  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-serif text-[27px] text-[var(--foreground)]">{t("Practice sets")}</h1>
        <p className="mt-1 text-[13px] text-[var(--muted-foreground)]">
          {isAdmin || isTeacher
            ? t("Write question sets, publish them, then put them in the learning market, and see how learners did.")
            : t("Practice sets you joined or were sent. Your answers go to your own question bank.")}
        </p>
      </header>
      {isAdmin || isTeacher ? (
        <AuthorTabs key={setParam ?? ""} setParam={setParam} tabParam={params?.get("tab") ?? null}
          courseParam={params?.get("course") ?? null} />
      ) : (
        <StudentView key={setParam ?? ""} initialSet={setParam} />
      )}
    </div>
  );
}

/**
 * Teachers and administrators write sets, and also learn from the ones they
 * joined (traceability #3). ``?set=`` of a set they do not own opens it to answer.
 */
function AuthorTabs({ setParam, tabParam, courseParam }: { setParam: string | null; tabParam: string | null; courseParam: string | null }) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<"mine" | "joined" | null>(() =>
    setParam ? null : tabParam === "joined" ? "joined" : "mine",
  );
  // The set named in the address opens once, in the tab it belongs to.
  const [openSet, setOpenSet] = useState<string | null>(setParam);

  useEffect(() => {
    if (tab !== null || !setParam) return;
    let alive = true;
    listMySets()
      .then((rows) => alive && setTab(rows.some((row) => row.id === setParam) ? "mine" : "joined"))
      .catch(() => alive && setTab("mine"));
    return () => {
      alive = false;
    };
  }, [tab, setParam]);

  if (tab === null) return <div aria-busy="true" className="h-24 animate-pulse rounded-xl bg-[var(--muted)]" />;
  const tabClass = (on: boolean) =>
    `rounded-lg px-3 py-1.5 text-[13px] ${on ? "bg-[var(--muted)] font-medium text-[var(--foreground)]" : "text-[var(--muted-foreground)] hover:bg-[var(--muted)]"}`;
  const choose = (next: "mine" | "joined") => {
    setOpenSet(null);
    setTab(next);
  };
  return (
    <div className="space-y-4">
      <div role="tablist" className="flex gap-1">
        <button type="button" role="tab" aria-selected={tab === "mine"} className={tabClass(tab === "mine")} onClick={() => choose("mine")}>
          {t("My practice sets")}
        </button>
        <button type="button" role="tab" aria-selected={tab === "joined"} className={tabClass(tab === "joined")} onClick={() => choose("joined")}>
          {t("Practice sets I joined")}
        </button>
      </div>
      {tab === "mine" ? (
        <AuthorView initialSet={openSet} courseParam={openSet ? courseParam : null} />
      ) : (
        <StudentView initialSet={openSet} />
      )}
    </div>
  );
}

const statusLabel: Record<PracticeSetSummary["status"], string> = {
  draft: "Draft",
  published: "Published",
  archived: "Archived",
};

function AuthorView({ initialSet, courseParam }: { initialSet: string | null; courseParam: string | null }) {
  const { t } = useTranslation();
  const { isAdmin } = useAuthStatus();
  const [sets, setSets] = useState<PracticeSetSummary[] | null>(null);
  // ``?set=`` of one's own set opens it (a remedial draft made from a report, batch 3 §3.1).
  const [selected, setSelected] = useState<string | null>(initialSet);
  // Follow-ups §3: ``&course=`` (from a course's report) goes with that set and the
  // remedial drafts made from it, so they can be added to the course once published.
  const [courseLink, setCourseLink] = useState<{ course: string; sets: string[] } | null>(() =>
    courseParam && initialSet ? { course: courseParam, sets: [initialSet] } : null,
  );
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
        {isAdmin && <WithdrawSection />}
      </aside>
      <section className="min-w-0">
        {selected ? (
          <SetEditor key={selected} setId={selected} onChanged={refresh} onDeleted={() => { setSelected(null); refresh(); }}
            courseId={courseLink?.sets.includes(selected) ? courseLink.course : null}
            onOpenSet={(id) => {
              setCourseLink((cur) => (cur && cur.sets.includes(selected) ? { ...cur, sets: [...cur.sets, id] } : cur));
              setSelected(id);
              refresh();
            }} />
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

/** Administrators withdraw other people's published sets; they never edit them (D10). */
function WithdrawSection() {
  const { t } = useTranslation();
  const [sets, setSets] = useState<(PracticeSetSummary & { owner: string })[] | null>(null);
  const [error, setError] = useState("");

  const refresh = useCallback(() => {
    listWithdrawable()
      .then(setSets)
      .catch((err) => setError(err instanceof Error ? t(err.message) : String(err)));
  }, [t]);
  useEffect(refresh, [refresh]);

  if (!sets?.length && !error) return null;
  return (
    <section className="space-y-2 border-t border-[var(--border)] pt-3">
      <h2 className="text-[12px] font-medium text-[var(--muted-foreground)]">{t("Published by others")}</h2>
      {error && <p role="alert" className="text-[12px] text-[var(--destructive)]">{error}</p>}
      <ul className="space-y-1">
        {(sets ?? []).map((set) => (
          <li key={set.id} className="flex items-center gap-2 rounded-lg px-3 py-2 text-[13px]">
            <span className="min-w-0 flex-1">
              <span className="block truncate font-medium text-[var(--foreground)]">{set.title}</span>
              <span className="text-[11.5px] text-[var(--muted-foreground)]">{set.owner}</span>
            </span>
            <button
              type="button"
              className={actionClass}
              onClick={() => {
                if (!window.confirm(t("Withdraw this practice set? Students will no longer see it."))) return;
                archiveSet(set.id)
                  .then(refresh)
                  .catch((err) => setError(err instanceof Error ? t(err.message) : String(err)));
              }}
            >
              {t("Withdraw")}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** The sets one joined from the market or was sent by name (remedial), to answer. */
function StudentView({ initialSet }: { initialSet: string | null }) {
  const { t } = useTranslation();
  const [sets, setSets] = useState<AssignedSet[] | null>(null);
  // ``?set=`` opens one directly (a notification, or the market's "Go study").
  const [selected, setSelected] = useState<string | null>(initialSet);
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
    return (
      <p className="text-[13px] text-[var(--muted-foreground)]">
        {t("Practice sets you join from the learning market appear here.")}{" "}
        <Link href="/market" className="text-[var(--primary)] hover:underline">{t("Learning market")}</Link>
      </p>
    );
  }
  return (
    <ul className="grid gap-3 sm:grid-cols-2">
      {sets.map((set) => (
        <li key={set.id} className="space-y-1.5 rounded-xl border border-[var(--border)] p-4">
          <p className="text-[14px] font-medium text-[var(--foreground)]">{set.title}</p>
          <p className="flex flex-wrap items-center gap-1.5 text-[12px] text-[var(--muted-foreground)]">
            <SourceBadge provenance={set.provenance ?? { kind: "teacher", name: set.teacher }} />
            {set.due_at ? t("Due {{date}}", { date: formatDate(set.due_at) }) : ""}
          </p>
          <p className="text-[12px] text-[var(--muted-foreground)]">
            {set.submissions === 0
              ? t("Not answered yet")
              : set.best_score === null
                ? t("Submitted · results after the deadline")
                : t("Best score {{score}} / {{max}}", { score: set.best_score, max: set.max_score })}
          </p>
          <button type="button" className={primaryClass} onClick={() => setSelected(set.id)}>
            {set.submissions > 0 ? t("Answer again") : t("Start")}
          </button>
        </li>
      ))}
    </ul>
  );
}
