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
}

export interface ClassDetail extends ClassSummary {
  can_manage: boolean;
  teachers: ClassPerson[];
  students?: ClassStudent[];
  pending_invitations?: { id: string; username: string; created_at: number }[];
  /** Class-wide student permissions; an absent key inherits the default. */
  student_policy?: Partial<Record<StudentPolicyKey, boolean>>;
}

export type StudentPolicyKey = "allow_personal_mastery" | "allow_private_kb";

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
  knowledge_points?: { name: string; mastery: number; mastered: boolean }[];
}

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
}

export interface StudentReport {
  id: string;
  username: string;
  teacher_paths: TeacherPathReport[];
  overall: OverallReport;
}

export interface ClassReport {
  class_id: string;
  class_name: string;
  students: StudentReport[];
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
export const updateClass = (id: string, patch: { name?: string; archived?: boolean }) =>
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
export const setClassStudentPolicy = (id: string, policy: Partial<Record<StudentPolicyKey, boolean>>) =>
  call<{ class: ClassDetail }>(`/${encodeURIComponent(id)}/student-policy`, {
    method: "PUT",
    body: JSON.stringify(policy),
  }).then((r) => r.class);
export const getClassReport = (id: string) => call<ClassReport>(`/${encodeURIComponent(id)}/report`);
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
