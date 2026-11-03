"use client";

import { Pin, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  createOrgItem,
  deleteAnnouncement,
  deleteOrgItem,
  getOrg,
  getTeacherDepartments,
  listAnnouncements,
  postAnnouncement,
  renameOrgItem,
  setTeacherDepartments,
  updateAnnouncement,
  updateClass,
  type Announcement,
  type ClassDetail,
  type OrgLists,
} from "@/lib/classes-api";

import { actionClass, inputClass, primaryClass } from "./ClassesHub";

const EMPTY: OrgLists = { departments: [], grades: [] };

/** The administrators' department and grade lists (spec 2026-11-03 batch 2 §0). */
export function useOrgLists(): [OrgLists, () => void] {
  const [lists, setLists] = useState<OrgLists>(EMPTY);
  const refresh = useCallback(() => {
    getOrg().then(setLists).catch(() => setLists(EMPTY));
  }, []);
  useEffect(refresh, [refresh]);
  return [lists, refresh];
}

/** A class's department and grade; its managers pick from the lists. */
export function ClassOrgFields({
  detail,
  run,
}: {
  detail: ClassDetail;
  run: (action: () => Promise<unknown>, message?: string) => Promise<void>;
}) {
  const { t } = useTranslation();
  const [lists] = useOrgLists();
  if (!lists.departments.length && !lists.grades.length) return null;
  const pick = (field: "department_id" | "grade_id", value: string) =>
    void run(() => updateClass(detail.id, { [field]: value }), t("Saved."));
  return (
    <div className="flex flex-wrap items-center gap-3 text-[12.5px]">
      <label className="flex items-center gap-1.5">
        {t("Department")}
        <select className={`${inputClass} w-auto`} value={detail.department_id ?? ""} disabled={!detail.can_manage}
          onChange={(e) => pick("department_id", e.target.value)}>
          <option value="">{t("Not set")}</option>
          {lists.departments.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
        </select>
      </label>
      <label className="flex items-center gap-1.5">
        {t("Grade")}
        <select className={`${inputClass} w-auto`} value={detail.grade_id ?? ""} disabled={!detail.can_manage}
          onChange={(e) => pick("grade_id", e.target.value)}>
          <option value="">{t("Not set")}</option>
          {lists.grades.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
        </select>
      </label>
    </div>
  );
}

/** A class's announcements: its managers post; its students read (spec 2026-11-03 batch 2 §4). */
export function AnnouncementsSection({ classId, canPost }: { classId: string; canPost: boolean }) {
  const { t } = useTranslation();
  const [items, setItems] = useState<Announcement[] | null>(null);
  const [text, setText] = useState("");
  const [pinned, setPinned] = useState(false);
  const [error, setError] = useState("");
  const refresh = useCallback(() => {
    listAnnouncements(classId).then(setItems).catch((err) => setError(err instanceof Error ? t(err.message) : String(err)));
  }, [classId, t]);
  useEffect(refresh, [refresh]);
  const act = (action: () => Promise<unknown>) =>
    action().then(refresh).catch((err) => setError(err instanceof Error ? t(err.message) : String(err)));

  return (
    <div className="space-y-2" data-testid="announcements">
      {canPost && (
        <form
          className="space-y-2"
          onSubmit={(event) => {
            event.preventDefault();
            void act(async () => {
              await postAnnouncement(classId, text.trim(), pinned);
              setText("");
              setPinned(false);
            });
          }}
        >
          <textarea aria-label={t("New announcement")} placeholder={t("New announcement")} rows={3} maxLength={5000}
            className={inputClass} value={text} onChange={(e) => setText(e.target.value)} />
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-1.5 text-[12px]">
              <input type="checkbox" checked={pinned} onChange={(e) => setPinned(e.target.checked)} />
              {t("Pin to top")}
            </label>
            <button type="submit" className={primaryClass} disabled={!text.trim()}>{t("Post")}</button>
          </div>
        </form>
      )}
      {error && <p role="alert" className="text-[12px] text-[var(--destructive)]">{error}</p>}
      {items === null ? null : items.length === 0 ? (
        <p className="text-[12px] text-[var(--muted-foreground)]">{t("No announcements yet.")}</p>
      ) : (
        <ul className="space-y-2">
          {items.map((item) => (
            <li key={item.id} className="rounded-lg border border-[var(--border)] p-2.5 text-[12.5px]">
              <p className="whitespace-pre-line text-[var(--foreground)]">
                {item.pinned && <Pin size={12} className="mr-1 inline text-[var(--primary)]" aria-label={t("Pinned")} />}
                {item.text}
              </p>
              <p className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-[var(--muted-foreground)]">
                {item.author || t("Former teacher")} · {new Date(item.created_at * 1000).toLocaleString()}
                {item.can_edit && (
                  <>
                    <button type="button" className="hover:underline" onClick={() => void act(() => updateAnnouncement(item.id, { pinned: !item.pinned }))}>
                      {item.pinned ? t("Unpin") : t("Pin to top")}
                    </button>
                    <button type="button" className="inline-flex items-center gap-0.5 text-red-600 hover:underline"
                      onClick={() => window.confirm(t("Delete this announcement?")) && void act(() => deleteAnnouncement(item.id))}>
                      <Trash2 size={11} />
                      {t("Delete")}
                    </button>
                  </>
                )}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Administrators keep the department and grade lists and list teachers under departments. */
export function OrgAdmin() {
  const { t } = useTranslation();
  const [lists, refresh] = useOrgLists();
  const [names, setNames] = useState({ department: "", grade: "" });
  const [teachers, setTeachers] = useState<Record<string, string[]>>({});
  const [people, setPeople] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const fail = (err: unknown) => setError(err instanceof Error ? t(err.message) : String(err));

  useEffect(() => {
    getTeacherDepartments()
      .then((r) => {
        setTeachers(r.teachers);
        setPeople(Object.fromEntries(r.people.map((p) => [p.id, p.username])));
      })
      .catch(() => setTeachers({}));
  }, []);

  const section = (kind: "department" | "grade", title: string, items: OrgLists["departments"]) => (
    <div className="space-y-2">
      <h4 className="text-[12.5px] font-medium">{title}</h4>
      <ul className="space-y-1">
        {items.map((item) => (
          <li key={item.id} className="flex items-center gap-2 text-[12.5px]">
            <input className={`${inputClass} w-48`} defaultValue={item.name} aria-label={t("Name")}
              onBlur={(e) => e.target.value.trim() && e.target.value !== item.name &&
                void renameOrgItem(kind, item.id, e.target.value.trim()).then(refresh).catch(fail)} />
            <button type="button" className={actionClass} aria-label={t("Delete")}
              onClick={() => window.confirm(t("Delete “{{name}}”? Classes using it will have none.", { name: item.name })) &&
                void deleteOrgItem(kind, item.id).then(refresh).catch(fail)}>
              <Trash2 size={12} />
            </button>
          </li>
        ))}
      </ul>
      <form className="flex gap-2" onSubmit={(e) => {
        e.preventDefault();
        void createOrgItem(kind, names[kind].trim()).then(() => { setNames({ ...names, [kind]: "" }); refresh(); }).catch(fail);
      }}>
        <input className={`${inputClass} w-48`} value={names[kind]} maxLength={60}
          placeholder={kind === "department" ? t("New department") : t("New grade")}
          onChange={(e) => setNames({ ...names, [kind]: e.target.value })} />
        <button type="submit" className={actionClass} disabled={!names[kind].trim()}>{t("Add")}</button>
      </form>
    </div>
  );

  return (
    <div className="space-y-4 rounded-xl border border-[var(--border)] p-4" data-testid="org-admin">
      <div className="grid gap-4 sm:grid-cols-2">
        {section("department", t("Departments"), lists.departments)}
        {section("grade", t("Grades"), lists.grades)}
      </div>
      {lists.departments.length > 0 && Object.keys(people).length > 0 && (
        <div className="space-y-1.5">
          <h4 className="text-[12.5px] font-medium">{t("Teachers by department")}</h4>
          <p className="text-[11.5px] text-[var(--muted-foreground)]">{t("Only for finding teachers; it grants nothing.")}</p>
          <ul className="space-y-1 text-[12.5px]">
            {Object.entries(people).map(([userId, username]) => (
              <li key={userId} className="flex flex-wrap items-center gap-2">
                <span className="w-28 truncate">{username}</span>
                {lists.departments.map((d) => {
                  const on = (teachers[userId] ?? []).includes(d.id);
                  return (
                    <label key={d.id} className="flex items-center gap-1">
                      <input type="checkbox" checked={on} onChange={() => {
                        const next = on ? (teachers[userId] ?? []).filter((x) => x !== d.id) : [...(teachers[userId] ?? []), d.id];
                        void setTeacherDepartments(userId, next).then(setTeachers).catch(fail);
                      }} />
                      {d.name}
                    </label>
                  );
                })}
              </li>
            ))}
          </ul>
        </div>
      )}
      {error && <p role="alert" className="text-[12px] text-[var(--destructive)]">{error}</p>}
    </div>
  );
}
