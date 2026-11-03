import { apiFetch, apiUrl } from "./api";
import { browserPath } from "./workspace-scope";

/** Classes are system-level: these routes are never workspace-scoped (spec 2026-10-29 §5). */
const BASE = "/api/classes";

export interface ClassPerson {
  id: string;
  username: string;
}

export interface ClassStudent extends ClassPerson {
  joined_via: "link" | "invitation" | "admin";
  joined_at: number;
}

export interface PendingInvitation extends ClassPerson {
  /** The invitation id (the student's own id is in `id` of the person). */
  invitation_id?: string;
  created_at: number;
}

export interface ClassSummary {
  id: string;
  name: string;
  archived: boolean;
  created_by: string;
  created_at: number;
  updated_at: number;
  student_count?: number;
  /** Spec 2026-11-03 batch 2 §0: "" when not set. */
  department_id?: string;
  grade_id?: string;
}

export interface OrgItem {
  id: string;
  name: string;
}

export interface OrgLists {
  departments: OrgItem[];
  grades: OrgItem[];
}

export interface Announcement {
  id: string;
  class_id: string;
  class_name: string;
  author: string;
  text: string;
  pinned: boolean;
  created_at: number;
  updated_at: number;
  can_edit: boolean;
}

export interface ClassDetail extends ClassSummary {
  can_manage: boolean;
  teachers: ClassPerson[];
  students?: ClassStudent[];
  pending_invitations?: { id: string; username: string; created_at: number }[];
  /** Class-wide student permissions; an absent key inherits the default. */
  student_policy?: StudentPolicy;
}

/** Per key: allow / deny, or absent to inherit; ``ai_daily_tokens`` 0 is no limit (batch 3 §8). */
export type StudentPolicy = Partial<Record<StudentPolicyKey, boolean>> & { ai_daily_tokens?: number };

export type StudentPolicyKey =
  | "allow_personal_mastery"
  | "allow_personal_book"
  | "allow_private_kb"
  // Spec 2026-11-03 batch 3 §7.1.
  | "allow_personal_course"
  | "allow_personal_task_card"
  | "allow_partners"
  | "allow_agents"
  | "allow_co_writer";

export interface AttemptTally {
  correct: number;
  partial: number;
  incorrect: number;
}

export interface TeacherPathReport {
  template_id: string;
  title: string;
  revision: number;
  kp_total: number;
  kp_mastered: number;
  average_mastery: number;
  attempts: AttemptTally;
  last_activity: number | null;
  knowledge_points?: {
    id: string;
    name: string;
    mastery: number;
    mastered: boolean;
    /** Batch 3 §6.2: answers and wrong answers on this point, and days stuck (not mastered). */
    answers?: number;
    wrong?: number;
    first_answered_at?: number | null;
    last_answered_at?: number | null;
    stuck_days?: number | null;
  }[];
}

/** Why a student needs attention (spec 2026-11-03 batch 3 §5.3). */
export type AttentionFlag =
  | { kind: "inactive"; days: number | null }
  | { kind: "overdue"; count: number }
  | { kind: "mistakes_rising"; recent: number; before: number }
  | { kind: "stuck"; kp_id: string; name: string; days: number; wrong: number; template_id: string };

export interface OverallReport {
  path_count: number;
  self_study_path_count: number;
  kp_total: number;
  kp_mastered: number;
  attempts: AttemptTally;
  last_activity: number | null;
  question_bank: { total: number; correct: number; partial: number; incorrect: number };
  study_minutes_total: number;
  study_minutes_7d: number;
  /** Batch 3 §6.3: the chosen period (``null``: all time) and the last 8 weeks. */
  period_days?: number | null;
  period_minutes?: number;
  period_answers?: number;
  trend?: { minutes: number; answers: number }[];
}

export interface PracticeSetResult {
  set_id: string;
  title: string;
  attempts: number;
  first_score: number;
  best_score: number;
  /** The maximum of the version the best score was on. */
  best_max_score: number;
  max_score: number;
  late: boolean;
  last_submitted_at: number;
}

export interface StudentReport {
  id: string;
  username: string;
  teacher_paths: TeacherPathReport[];
  overall: OverallReport;
  /** Spec D17: this teacher's practice sets the student submitted. */
  practice_sets?: PracticeSetResult[];
  flags?: AttentionFlag[];
}

export interface ClassReport {
  class_id: string;
  class_name: string;
  period_days?: number | null;
  students: StudentReport[];
}

export interface ClassPathPoints {
  template_id: string;
  title: string;
  points: { id: string; name: string; students: number; mastered: number; not_mastered: string[] }[];
}

export interface InviteLink {
  token: string;
  created_at: number;
  expires_at: number | null;
}

export interface MyInvitation {
  id: string;
  class_id: string;
  class_name: string;
  invited_by: ClassPerson;
  created_at: number;
}

export class ClassesApiError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message);
    this.name = "ClassesApiError";
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await apiFetch(apiUrl(`${BASE}${path}`), {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json", ...init?.headers } : init?.headers,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    const detail = typeof data?.detail === "string" ? data.detail : `Request failed (${res.status})`;
    throw new ClassesApiError(detail, res.status);
  }
  return (await res.json()) as T;
}

const json = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export const listClasses = (includeArchived = false) =>
  call<{ classes: ClassSummary[] }>(includeArchived ? "?include_archived=true" : "").then((r) => r.classes);
export const createClass = (name: string) => call<{ class: ClassDetail }>("", json({ name })).then((r) => r.class);
export const getClass = (id: string) => call<{ class: ClassDetail }>(`/${encodeURIComponent(id)}`).then((r) => r.class);
export const updateClass = (
  id: string,
  patch: { name?: string; archived?: boolean; department_id?: string; grade_id?: string },
) =>
  call<{ class: ClassDetail }>(`/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(patch) }).then(
    (r) => r.class,
  );

export const searchStudents = (q: string) =>
  call<{ students: ClassPerson[] }>(`/students/search?q=${encodeURIComponent(q)}`).then((r) => r.students);
export const searchTeachers = (q: string) =>
  call<{ teachers: ClassPerson[] }>(`/teachers/search?q=${encodeURIComponent(q)}`).then((r) => r.teachers);

export const addTeacher = (id: string, userId: string) =>
  call<{ class: ClassDetail }>(`/${encodeURIComponent(id)}/teachers`, json({ user_id: userId })).then((r) => r.class);
export const removeTeacher = (id: string, userId: string) =>
  call<{ class: ClassDetail }>(`/${encodeURIComponent(id)}/teachers/${encodeURIComponent(userId)}`, {
    method: "DELETE",
  });
export const addStudents = (id: string, studentIds: string[]) =>
  call<{ class: ClassDetail }>(`/${encodeURIComponent(id)}/students`, json({ student_ids: studentIds })).then(
    (r) => r.class,
  );
export const removeStudent = (id: string, studentId: string) =>
  call<{ class: ClassDetail }>(`/${encodeURIComponent(id)}/students/${encodeURIComponent(studentId)}`, {
    method: "DELETE",
  }).then((r) => r.class);
export const setClassStudentPolicy = (id: string, policy: StudentPolicy) =>
  call<{ class: ClassDetail }>(`/${encodeURIComponent(id)}/student-policy`, {
    method: "PUT",
    body: JSON.stringify(policy),
  }).then((r) => r.class);
export const getClassReport = (id: string, days?: number | null) =>
  call<ClassReport>(`/${encodeURIComponent(id)}/report${days ? `?days=${days}` : ""}`);
export const classReportExportUrl = (id: string, days?: number | null) =>
  apiUrl(`${BASE}/${encodeURIComponent(id)}/report/export${days ? `?days=${days}` : ""}`);
export const getClassPathPoints = (id: string, templateId: string) =>
  call<ClassPathPoints>(`/${encodeURIComponent(id)}/report/paths/${encodeURIComponent(templateId)}`);
export const getStudentReport = (id: string, studentId: string) =>
  call<StudentReport>(`/${encodeURIComponent(id)}/students/${encodeURIComponent(studentId)}/report`);
export const leaveClass = (id: string) => call<{ ok: boolean }>(`/${encodeURIComponent(id)}/leave`, { method: "POST" });

export const inviteStudent = (id: string, studentId: string) =>
  call<{ invitation: { id: string } }>(`/${encodeURIComponent(id)}/invitations`, json({ student_id: studentId }));
export const cancelInvitation = (invitationId: string) =>
  call<{ ok: boolean }>(`/invitations/${encodeURIComponent(invitationId)}`, { method: "DELETE" });
export const myInvitations = () => call<{ invitations: MyInvitation[] }>("/invitations/mine").then((r) => r.invitations);
export const acceptInvitation = (invitationId: string) =>
  call<{ ok: boolean }>(`/invitations/${encodeURIComponent(invitationId)}/accept`, { method: "POST" });
export const declineInvitation = (invitationId: string) =>
  call<{ ok: boolean }>(`/invitations/${encodeURIComponent(invitationId)}/decline`, { method: "POST" });

export const getInviteLink = (id: string) =>
  call<{ link: InviteLink | null }>(`/${encodeURIComponent(id)}/invite-link`).then((r) => r.link);
export const regenerateInviteLink = (id: string, expiresInDays: number | null) =>
  call<{ link: InviteLink }>(`/${encodeURIComponent(id)}/invite-link`, json({ expires_in_days: expiresInDays })).then(
    (r) => r.link,
  );
export const revokeInviteLink = (id: string) =>
  call<{ ok: boolean }>(`/${encodeURIComponent(id)}/invite-link`, { method: "DELETE" });
export const joinClass = (token: string) =>
  call<{ class_id: string; class_name: string }>("/join", json({ token }));
export const resetStudentPassword = (studentId: string, newPassword: string) =>
  call<{ ok: boolean }>(`/students/${encodeURIComponent(studentId)}/password`, json({ new_password: newPassword }));

/** The QR code image for an invite URL (an SVG served by the backend). */
// Departments and grades (spec 2026-11-03 batch 2 §0).
export const getOrg = () => call<OrgLists>("/org");
export const createOrgItem = (kind: "department" | "grade", name: string) =>
  call<{ item: OrgItem }>(`/org/${kind}`, json({ name })).then((r) => r.item);
export const renameOrgItem = (kind: "department" | "grade", id: string, name: string) =>
  call<OrgLists>(`/org/${kind}/${encodeURIComponent(id)}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) });
export const deleteOrgItem = (kind: "department" | "grade", id: string) =>
  call<OrgLists>(`/org/${kind}/${encodeURIComponent(id)}`, { method: "DELETE" });
export const getTeacherDepartments = () =>
  call<{ teachers: Record<string, string[]>; people: ClassPerson[] }>("/org/teachers");
export const setTeacherDepartments = (userId: string, departmentIds: string[]) =>
  call<{ teachers: Record<string, string[]> }>(`/org/teachers/${encodeURIComponent(userId)}`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ department_ids: departmentIds }),
  }).then((r) => r.teachers);

// Announcements (spec 2026-11-03 batch 2 §4).
export const listAnnouncements = (id: string) =>
  call<{ announcements: Announcement[] }>(`/${encodeURIComponent(id)}/announcements`).then((r) => r.announcements);
export const postAnnouncement = (id: string, text: string, pinned: boolean) =>
  call<{ announcement: Announcement }>(`/${encodeURIComponent(id)}/announcements`, json({ text, pinned })).then((r) => r.announcement);
export const updateAnnouncement = (announcementId: string, patch: { text?: string; pinned?: boolean }) =>
  call<{ announcement: Announcement }>(`/announcements/${encodeURIComponent(announcementId)}`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch),
  }).then((r) => r.announcement);
export const deleteAnnouncement = (announcementId: string) =>
  call<{ ok: boolean }>(`/announcements/${encodeURIComponent(announcementId)}`, { method: "DELETE" });

export const inviteQrUrl = (inviteUrl: string) => apiUrl(`${BASE}/qr.svg?data=${encodeURIComponent(inviteUrl)}`);

/** The public preview of an invite link (no sign-in needed). */
export async function inviteLinkInfo(token: string): Promise<{ class_name: string; expires_at: number | null }> {
  const res = await apiFetch(apiUrl(`/api/class-invites/${encodeURIComponent(token)}`));
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new ClassesApiError(typeof data?.detail === "string" ? data.detail : "Invalid invite link", res.status);
  }
  return res.json();
}

/** The absolute page URL a student opens to join (honours a sub-path deployment). */
export function inviteUrl(token: string): string {
  const origin = typeof window === "undefined" ? "" : window.location.origin;
  return `${origin}${browserPath(`/join/${encodeURIComponent(token)}`)}`;
}
