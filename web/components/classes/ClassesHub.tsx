"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, Copy, Plus, Users, X } from "lucide-react";

import { useAuthStatus } from "@/hooks/useAuthStatus";
import {
  acceptInvitation,
  createClass,
  declineInvitation,
  leaveClass,
  listClasses,
  myInvitations,
  type ClassSummary,
  type MyInvitation,
} from "@/lib/classes-api";
import { AnnouncementsSection, OrgAdmin, useOrgLists } from "./ClassOrg";
import { ClassPanel } from "./ClassPanel";

export const actionClass =
  "inline-flex h-8 items-center gap-1.5 rounded-lg border border-[var(--border)] px-3 text-[12.5px] font-medium text-[var(--foreground)] transition-colors hover:bg-[var(--muted)] disabled:cursor-not-allowed disabled:opacity-50";
export const primaryClass =
  "inline-flex h-8 items-center gap-1.5 rounded-lg bg-[var(--primary)] px-3.5 text-[12.5px] font-semibold text-[var(--primary-foreground)] transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50";
export const inputClass =
  "h-9 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 text-[13px] text-[var(--foreground)] outline-none focus:border-[var(--ring)]";

/**
 * Classes (spec 2026-10-29 §5): teachers and administrators manage classes;
 * students see their classes and answer teacher invitations.
 */
export default function ClassesHub() {
  const { t } = useTranslation();
  const { isAdmin, isTeacher, loading } = useAuthStatus();
  const manages = isAdmin || isTeacher;

  if (loading) return <div aria-busy="true" className="h-40 animate-pulse rounded-xl bg-[var(--muted)]" />;
  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-serif text-[27px] text-[var(--foreground)]">{t("Classes")}</h1>
        <p className="mt-1 text-[12.5px] text-[var(--muted-foreground)]">
          {manages
            ? t("Create classes, invite students with a link or QR code, and manage who is in each class.")
            : t("Join classes from your teachers and see the classes you are in.")}
        </p>
      </header>
      {manages ? <ManagerView /> : <StudentView />}
    </div>
  );
}

function ManagerView() {
  const { t } = useTranslation();
  const { isAdmin } = useAuthStatus();
  const [lists] = useOrgLists();
  const [showOrg, setShowOrg] = useState(false);
  const [filter, setFilter] = useState({ department: "", grade: "" });
  const [classes, setClasses] = useState<ClassSummary[] | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  // ``?class=`` opens one directly (from the teacher's workbench, batch 3 §5.1).
  const [selected, setSelected] = useState<string | null>(() =>
    typeof window === "undefined" ? null : new URLSearchParams(window.location.search).get("class"),
  );
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    try {
      setClasses(await listClasses(showArchived));
      setError("");
    } catch (err) {
      setError(err instanceof Error ? t(err.message) : String(err));
    }
  }, [showArchived, t]);
  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    try {
      const created = await createClass(name.trim());
      setName("");
      setCreating(false);
      await refresh();
      setSelected(created.id);
    } catch (err) {
      setError(err instanceof Error ? t(err.message) : String(err));
    }
  }

  const shown = (classes ?? []).filter(
    (row) =>
      (!filter.department || row.department_id === filter.department) &&
      (!filter.grade || row.grade_id === filter.grade),
  );

  return (
    <div className="space-y-4">
    {isAdmin && (
      <div className="space-y-2">
        <button type="button" className={actionClass} onClick={() => setShowOrg(!showOrg)}>
          {t("Departments and grades")}
        </button>
        {showOrg && <OrgAdmin />}
      </div>
    )}
    <div className="grid gap-6 lg:grid-cols-[280px_minmax(0,1fr)]">
      <section className="space-y-3">
        <div className="flex items-center justify-between gap-2">
          <button type="button" className={primaryClass} onClick={() => setCreating(!creating)}>
            <Plus size={14} />
            {t("New class")}
          </button>
          <label className="flex items-center gap-1.5 text-[12px] text-[var(--muted-foreground)]">
            <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />
            {t("Show archived")}
          </label>
        </div>
        {creating && (
          <form onSubmit={submit} className="space-y-2 rounded-xl border border-[var(--border)] p-3">
            <input
              autoFocus
              className={inputClass}
              value={name}
              maxLength={100}
              onChange={(e) => setName(e.target.value)}
              placeholder={t("Class name, e.g. Physics Class 1")}
            />
            <div className="flex gap-2">
              <button type="submit" className={primaryClass} disabled={!name.trim()}>
                {t("Create class")}
              </button>
              <button type="button" className={actionClass} onClick={() => setCreating(false)}>
                {t("Cancel")}
              </button>
            </div>
          </form>
        )}
        {(lists.departments.length > 0 || lists.grades.length > 0) && (
          <div className="flex gap-2">
            <select aria-label={t("Department")} className={inputClass} value={filter.department}
              onChange={(e) => setFilter({ ...filter, department: e.target.value })}>
              <option value="">{t("All departments")}</option>
              {lists.departments.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
            <select aria-label={t("Grade")} className={inputClass} value={filter.grade}
              onChange={(e) => setFilter({ ...filter, grade: e.target.value })}>
              <option value="">{t("All grades")}</option>
              {lists.grades.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
            </select>
          </div>
        )}
        {error && <p role="alert" className="text-[12px] text-[var(--destructive)]">{error}</p>}
        {classes === null ? (
          <div aria-busy="true" className="h-24 animate-pulse rounded-xl bg-[var(--muted)]" />
        ) : classes.length === 0 ? (
          <p className="rounded-xl border border-dashed border-[var(--border)] p-6 text-center text-[12.5px] text-[var(--muted-foreground)]">
            {t("No classes yet. Create one to start inviting students.")}
          </p>
        ) : (
          <ul className="space-y-1.5">
            {shown.map((row) => (
              <li key={row.id}>
                <button
                  type="button"
                  onClick={() => setSelected(row.id)}
                  className={`flex w-full items-center justify-between gap-2 rounded-xl border px-3 py-2.5 text-left transition-colors ${
                    selected === row.id
                      ? "border-[var(--primary)] bg-[var(--primary)]/5"
                      : "border-[var(--border)] hover:border-[var(--ring)]"
                  }`}
                >
                  <span className="min-w-0 truncate text-[13.5px] font-medium text-[var(--foreground)]">{row.name}</span>
                  <span className="flex shrink-0 items-center gap-1.5 text-[11.5px] text-[var(--muted-foreground)]">
                    {row.archived && <span className="rounded-full bg-[var(--muted)] px-1.5">{t("Archived")}</span>}
                    <Users size={12} />
                    {row.student_count ?? 0}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section className="min-w-0">
        {selected ? (
          <ClassPanel key={selected} classId={selected} onChanged={refresh} />
        ) : (
          <p className="rounded-xl border border-dashed border-[var(--border)] p-10 text-center text-[12.5px] text-[var(--muted-foreground)]">
            {t("Select a class to manage its students, teachers and invite link.")}
          </p>
        )}
      </section>
    </div>
    </div>
  );
}

function StudentView() {
  const { t } = useTranslation();
  const [classes, setClasses] = useState<ClassSummary[] | null>(null);
  const [invitations, setInvitations] = useState<MyInvitation[]>([]);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [mine, invites] = await Promise.all([listClasses(), myInvitations()]);
      setClasses(mine);
      setInvitations(invites);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? t(err.message) : String(err));
    }
  }, [t]);
  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function run(action: () => Promise<unknown>) {
    try {
      await action();
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? t(err.message) : String(err));
    }
  }

  return (
    <div className="space-y-6">
      {error && <p role="alert" className="text-[12px] text-[var(--destructive)]">{error}</p>}
      {invitations.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-[13.5px] font-medium text-[var(--foreground)]">{t("Invitations")}</h2>
          {invitations.map((invitation) => (
            <div
              key={invitation.id}
              className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[var(--border)] p-3"
            >
              <p className="text-[13px] text-[var(--foreground)]">
                {t("{{teacher}} invited you to join {{name}}", {
                  teacher: invitation.invited_by.username,
                  name: invitation.class_name,
                })}
              </p>
              <div className="flex gap-2">
                <button type="button" className={primaryClass} onClick={() => void run(() => acceptInvitation(invitation.id))}>
                  <Check size={14} />
                  {t("Accept")}
                </button>
                <button type="button" className={actionClass} onClick={() => void run(() => declineInvitation(invitation.id))}>
                  <X size={14} />
                  {t("Decline")}
                </button>
              </div>
            </div>
          ))}
        </section>
      )}
      <section className="space-y-2">
        <h2 className="text-[13.5px] font-medium text-[var(--foreground)]">{t("My classes")}</h2>
        {classes === null ? (
          <div aria-busy="true" className="h-24 animate-pulse rounded-xl bg-[var(--muted)]" />
        ) : classes.length === 0 ? (
          <p className="rounded-xl border border-dashed border-[var(--border)] p-6 text-center text-[12.5px] text-[var(--muted-foreground)]">
            {t("You are not in any class yet. Open an invite link from your teacher to join one.")}
          </p>
        ) : (
          classes.map((row) => (
            <div key={row.id} className="space-y-2 rounded-xl border border-[var(--border)] p-3">
            <div className="flex items-center justify-between gap-3">
              <span className="text-[13.5px] font-medium text-[var(--foreground)]">{row.name}</span>
              <button
                type="button"
                className={actionClass}
                onClick={() => {
                  if (window.confirm(t("Leave {{name}}?", { name: row.name }))) void run(() => leaveClass(row.id));
                }}
              >
                {t("Leave class")}
              </button>
            </div>
            <details>
              <summary className="cursor-pointer text-[12px] text-[var(--muted-foreground)]">{t("Announcements")}</summary>
              <div className="pt-2"><AnnouncementsSection classId={row.id} canPost={false} /></div>
            </details>
            </div>
          ))
        )}
      </section>
    </div>
  );
}

export function CopyButton({ text }: { text: string }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className={actionClass}
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(() => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1500);
        });
      }}
    >
      {copied ? <Check size={14} /> : <Copy size={14} />}
      {copied ? t("Copied") : t("Copy link")}
    </button>
  );
}

