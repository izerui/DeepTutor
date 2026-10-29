"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { KeyRound, Link2, RefreshCw, Trash2, UserPlus, X } from "lucide-react";

import { useAuthStatus } from "@/hooks/useAuthStatus";
import {
  addStudents,
  addTeacher,
  cancelInvitation,
  getClass,
  getInviteLink,
  inviteQrUrl,
  inviteStudent,
  inviteUrl,
  regenerateInviteLink,
  removeStudent,
  removeTeacher,
  resetStudentPassword,
  revokeInviteLink,
  searchStudents,
  searchTeachers,
  setClassStudentPolicy,
  updateClass,
  type ClassDetail,
  type ClassPerson,
  type InviteLink,
  type StudentPolicyKey,
} from "@/lib/classes-api";
import { actionClass, CopyButton, inputClass, primaryClass } from "./ClassesHub";
import { ClassReportSection } from "./ClassReport";

const sectionClass = "space-y-3 rounded-xl border border-[var(--border)] p-4";
const titleClass = "text-[13.5px] font-medium text-[var(--foreground)]";
const hintClass = "text-[12px] leading-relaxed text-[var(--muted-foreground)]";

export function ClassPanel({ classId, onChanged }: { classId: string; onChanged: () => void }) {
  const { t } = useTranslation();
  const { isAdmin, userId } = useAuthStatus();
  const [detail, setDetail] = useState<ClassDetail | null>(null);
  const [link, setLink] = useState<InviteLink | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const next = await getClass(classId);
      setDetail(next);
      if (next.can_manage) setLink(await getInviteLink(classId));
      setError("");
    } catch (err) {
      setError(err instanceof Error ? t(err.message) : String(err));
    }
  }, [classId, t]);
  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function run(action: () => Promise<unknown>, message = "") {
    setBusy(true);
    setNotice("");
    try {
      await action();
      await refresh();
      onChanged();
      setNotice(message);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? t(err.message) : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (!detail) {
    return error ? (
      <p role="alert" className="text-[12px] text-[var(--destructive)]">{error}</p>
    ) : (
      <div aria-busy="true" className="h-60 animate-pulse rounded-xl bg-[var(--muted)]" />
    );
  }

  return (
    <fieldset disabled={busy} className="min-w-0 space-y-4">
      <ClassHeader detail={detail} run={run} />
      {error && <p role="alert" className="text-[12px] text-[var(--destructive)]">{error}</p>}
      {notice && <p role="status" className="text-[12px] text-[var(--muted-foreground)]">{notice}</p>}

      <section className={sectionClass}>
        <h3 className={titleClass}>{t("Invite link")}</h3>
        <InviteLinkSection detail={detail} link={link} run={run} />
      </section>

      {/* An archived class admits nobody (spec §5). */}
      {!detail.archived && (
      <section className={sectionClass}>
          <h3 className={titleClass}>{t("Invite students")}</h3>
          <p className={hintClass}>{t("Search by username. The student joins after accepting the invitation.")}</p>
          <PersonSearch
            search={searchStudents}
            actionLabel={t("Invite")}
            onPick={(person) => run(() => inviteStudent(detail.id, person.id), t("Invitation sent."))}
          />
          {(detail.pending_invitations ?? []).length > 0 && (
            <ul className="space-y-1">
              {(detail.pending_invitations ?? []).map((invitation) => (
                <li key={invitation.id} className="flex items-center justify-between gap-2 text-[12.5px]">
                  <span className="text-[var(--foreground)]">
                    {invitation.username} · <span className="text-[var(--muted-foreground)]">{t("Waiting for reply")}</span>
                  </span>
                  <button type="button" className={actionClass} onClick={() => void run(() => cancelInvitation(invitation.id))}>
                    <X size={13} />
                    {t("Cancel invitation")}
                  </button>
                </li>
              ))}
            </ul>
          )}
          {isAdmin && (
            <div className="space-y-2 border-t border-[var(--border)] pt-3">
              <p className={hintClass}>{t("Administrators can also add students directly, without an invitation.")}</p>
              <PersonSearch
                search={searchStudents}
                actionLabel={t("Add")}
                onPick={(person) => run(() => addStudents(detail.id, [person.id]))}
              />
            </div>
          )}
        </section>
      )}

      <section className={sectionClass}>
        <h3 className={titleClass}>
          {t("Students")} <span className="text-[var(--muted-foreground)]">({detail.students?.length ?? 0})</span>
        </h3>
        {(detail.students ?? []).length === 0 ? (
          <p className={hintClass}>{t("No students yet.")}</p>
        ) : (
          <ul className="divide-y divide-[var(--border)]">
            {(detail.students ?? []).map((student) => (
              <StudentRow key={student.id} classId={detail.id} student={student} run={run} />
            ))}
          </ul>
        )}
      </section>

      {/* Spec 2026-10-29 D17: teachers read learning reports; administrators do not. */}
      {!isAdmin && (
        <section className={sectionClass}>
          <h3 className={titleClass}>{t("Learning report")}</h3>
          <ClassReportSection classId={detail.id} />
        </section>
      )}

      {isAdmin && <StudentPolicySection detail={detail} run={run} />}

      <section className={sectionClass}>
        <h3 className={titleClass}>{t("Teachers")}</h3>
        <ul className="space-y-1">
          {detail.teachers.map((teacher) => (
            <li key={teacher.id} className="flex items-center justify-between gap-2 text-[12.5px]">
              <span className="text-[var(--foreground)]">{teacher.username}</span>
              {/* Spec §5: administrators change who teaches; a teacher may step down if another remains. */}
              {(isAdmin || (teacher.id === userId && detail.teachers.length > 1)) && <button
                type="button"
                className={actionClass}
                onClick={() => {
                  if (window.confirm(t("Remove {{name}} from this class's teachers?", { name: teacher.username }))) {
                    void run(() => removeTeacher(detail.id, teacher.id));
                  }
                }}
              >
                <Trash2 size={13} />
                {teacher.id === userId ? t("Leave as teacher") : t("Remove")}
              </button>}
            </li>
          ))}
        </ul>
        <PersonSearch
          search={searchTeachers}
          actionLabel={t("Add teacher")}
          onPick={(person) => run(() => addTeacher(detail.id, person.id))}
        />
      </section>
    </fieldset>
  );
}

type Run = (action: () => Promise<unknown>, message?: string) => Promise<void>;

function ClassHeader({ detail, run }: { detail: ClassDetail; run: Run }) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(detail.name);
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      {editing ? (
        <form
          className="flex min-w-0 flex-1 gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void run(() => updateClass(detail.id, { name: name.trim() })).then(() => setEditing(false));
          }}
        >
          <input autoFocus className={inputClass} value={name} maxLength={100} onChange={(e) => setName(e.target.value)} />
          <button type="submit" className={primaryClass} disabled={!name.trim()}>
            {t("Save")}
          </button>
          <button type="button" className={actionClass} onClick={() => setEditing(false)}>
            {t("Cancel")}
          </button>
        </form>
      ) : (
        <h2 className="min-w-0 truncate text-[18px] font-semibold text-[var(--foreground)]">{detail.name}</h2>
      )}
      {!editing && (
        <div className="flex gap-2">
          <button type="button" className={actionClass} onClick={() => setEditing(true)}>
            {t("Rename")}
          </button>
          <button
            type="button"
            className={actionClass}
            onClick={() => {
              if (
                detail.archived ||
                window.confirm(
                  t("Archive this class? Its invite link and pending invitations end, and students lose what was published to this class. Records are kept."),
                )
              ) {
                void run(() => updateClass(detail.id, { archived: !detail.archived }));
              }
            }}
          >
            {detail.archived ? t("Restore class") : t("Archive class")}
          </button>
        </div>
      )}
    </div>
  );
}

function InviteLinkSection({ detail, link, run }: { detail: ClassDetail; link: InviteLink | null; run: Run }) {
  const { t } = useTranslation();
  const [expiry, setExpiry] = useState<string>("");
  const url = link ? inviteUrl(link.token) : "";
  const generate = () =>
    run(() => regenerateInviteLink(detail.id, expiry ? Number(expiry) : null), t("A new invite link is ready."));

  if (detail.archived) return <p className={hintClass}>{t("Archived classes cannot be joined.")}</p>;
  return (
    <div className="space-y-3">
      <p className={hintClass}>
        {t("Students open the link or scan the QR code, sign in or create an account, and join this class.")}
      </p>
      {link ? (
        <div className="flex flex-wrap items-start gap-4">
          <img
            src={inviteQrUrl(url)}
            alt={t("QR code for the invite link")}
            className="h-36 w-36 rounded-lg border border-[var(--border)] bg-white p-1"
          />
          <div className="min-w-0 flex-1 space-y-2">
            <p className="select-all break-all rounded-lg bg-[var(--muted)]/50 px-2.5 py-2 font-mono text-[11.5px] text-[var(--foreground)]">
              {url}
            </p>
            <p className={hintClass}>
              {link.expires_at
                ? t("Expires on {{date}}", { date: new Date(link.expires_at * 1000).toLocaleDateString() })
                : t("Does not expire")}
            </p>
            <div className="flex flex-wrap gap-2">
              <CopyButton text={url} />
              <button
                type="button"
                className={actionClass}
                onClick={() => {
                  if (window.confirm(t("Create a new link? The current link stops working immediately."))) void generate();
                }}
              >
                <RefreshCw size={13} />
                {t("New link")}
              </button>
              <button
                type="button"
                className={actionClass}
                onClick={() => {
                  if (window.confirm(t("Turn off the invite link? Students already in the class stay."))) {
                    void run(() => revokeInviteLink(detail.id));
                  }
                }}
              >
                <X size={13} />
                {t("Turn off link")}
              </button>
            </div>
          </div>
        </div>
      ) : (
        <p className={hintClass}>{t("No invite link is active.")}</p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-2 text-[12px] text-[var(--muted-foreground)]">
          {t("Link expires after")}
          <select
            value={expiry}
            onChange={(e) => setExpiry(e.target.value)}
            className="h-8 rounded-lg border border-[var(--border)] bg-[var(--background)] px-2 text-[12px] text-[var(--foreground)]"
          >
            <option value="">{t("Never")}</option>
            <option value="1">{t("1 day")}</option>
            <option value="7">{t("7 days")}</option>
            <option value="30">{t("30 days")}</option>
          </select>
        </label>
        {!link && (
          <button type="button" className={primaryClass} onClick={() => void generate()}>
            <Link2 size={13} />
            {t("Create invite link")}
          </button>
        )}
      </div>
    </div>
  );
}

const POLICY_ROWS: { key: StudentPolicyKey; label: string }[] = [
  { key: "allow_personal_mastery", label: "Create their own Mastery Paths" },
  { key: "allow_personal_book", label: "Create their own books" },
  { key: "allow_private_kb", label: "Create their own knowledge bases" },
];

function StudentPolicySection({ detail, run }: { detail: ClassDetail; run: Run }) {
  const { t } = useTranslation();
  const policy = detail.student_policy ?? {};
  const choose = (key: StudentPolicyKey, value: string) => {
    const next: Partial<Record<StudentPolicyKey, boolean>> = { ...policy };
    if (value === "") delete next[key];
    else next[key] = value === "allow";
    void run(() => setClassStudentPolicy(detail.id, next), t("Student permissions saved."));
  };
  return (
    <section className={sectionClass}>
      <h3 className={titleClass}>{t("Student permissions")}</h3>
      <p className={hintClass}>
        {t("Applies to every student in this class. A setting made for one student wins; across classes, any class that allows, allows.")}
      </p>
      {POLICY_ROWS.map((row) => (
        <label key={row.key} className="flex flex-wrap items-center justify-between gap-2 text-[12.5px] text-[var(--foreground)]">
          {t(row.label)}
          <select
            value={policy[row.key] === undefined ? "" : policy[row.key] ? "allow" : "deny"}
            onChange={(e) => choose(row.key, e.target.value)}
            className="h-8 rounded-lg border border-[var(--border)] bg-[var(--background)] px-2 text-[12px]"
          >
            <option value="">{t("Default")}</option>
            <option value="allow">{t("Allow")}</option>
            <option value="deny">{t("Do not allow")}</option>
          </select>
        </label>
      ))}
    </section>
  );
}

function StudentRow({ classId, student, run }: { classId: string; student: ClassPerson; run: Run }) {
  const { t } = useTranslation();
  const [resetting, setResetting] = useState(false);
  const [password, setPassword] = useState("");
  return (
    <li className="py-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-[13px] text-[var(--foreground)]">{student.username}</span>
        <div className="flex gap-2">
          <button type="button" className={actionClass} onClick={() => setResetting(!resetting)}>
            <KeyRound size={13} />
            {t("Reset password")}
          </button>
          <button
            type="button"
            className={actionClass}
            onClick={() => {
              if (window.confirm(t("Remove {{name}} from this class?", { name: student.username }))) {
                void run(() => removeStudent(classId, student.id));
              }
            }}
          >
            <Trash2 size={13} />
            {t("Remove")}
          </button>
        </div>
      </div>
      {resetting && (
        <form
          className="mt-2 flex flex-wrap gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void run(
              () => resetStudentPassword(student.id, password),
              t("Password updated. Tell the student their new password."),
            ).then(() => {
              setPassword("");
              setResetting(false);
            });
          }}
        >
          <input
            type="password"
            autoComplete="new-password"
            className={`${inputClass} max-w-xs`}
            value={password}
            minLength={8}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={t("New password (at least 8 characters)")}
          />
          <button type="submit" className={primaryClass} disabled={password.length < 8}>
            {t("Save")}
          </button>
        </form>
      )}
    </li>
  );
}

function PersonSearch({
  search,
  actionLabel,
  onPick,
}: {
  search: (query: string) => Promise<ClassPerson[]>;
  actionLabel: string;
  onPick: (person: ClassPerson) => Promise<void> | void;
}) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<ClassPerson[]>([]);

  useEffect(() => {
    const needle = query.trim();
    if (!needle) {
      setResults([]);
      return;
    }
    let alive = true;
    const timer = window.setTimeout(() => {
      void search(needle)
        .then((rows) => {
          if (alive) setResults(rows);
        })
        .catch(() => {
          if (alive) setResults([]);
        });
    }, 250);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [query, search]);

  return (
    <div className="space-y-1.5">
      <input
        className={inputClass}
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder={t("Search by username")}
      />
      {results.length > 0 && (
        <ul className="max-h-48 space-y-1 overflow-y-auto">
          {results.map((person) => (
            <li key={person.id} className="flex items-center justify-between gap-2 text-[12.5px]">
              <span className="text-[var(--foreground)]">{person.username}</span>
              <button
                type="button"
                className={actionClass}
                onClick={() => {
                  void Promise.resolve(onPick(person)).then(() => setQuery(""));
                }}
              >
                <UserPlus size={13} />
                {actionLabel}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
