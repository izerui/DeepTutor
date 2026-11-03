import { apiFetch, apiUrl } from "./api";

/** A teacher's comment on one student's work (spec 2026-11-03 batch 2 §3). */
export interface TeacherComment {
  id: string;
  /** "" once the author's account is gone. */
  author: string;
  student_id: string;
  kind: CommentKind;
  ref: string;
  text: string;
  created_at: number;
  updated_at: number;
}

export type CommentKind = "practice_submission" | "path_point";

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await apiFetch(apiUrl(`/api/comments${path}`), {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json", ...init?.headers } : init?.headers,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(typeof data?.detail === "string" ? data.detail : `Request failed (${res.status})`);
  }
  return (await res.json()) as T;
}

const query = (values: Record<string, string>) => new URLSearchParams(values).toString();

/** The current student's comments on one practice set or Mastery Path (template id). */
export const myComments = (kind: CommentKind, subject: string) =>
  call<{ comments: TeacherComment[] }>(`/mine?${query({ kind, subject })}`).then((r) => r.comments);
/** What the current teacher wrote to one student on one set or path. */
export const writtenComments = (kind: CommentKind, subject: string, studentId: string) =>
  call<{ comments: TeacherComment[] }>(`/written?${query({ kind, subject, student_id: studentId })}`).then((r) => r.comments);
export const writeComment = (kind: CommentKind, ref: string, studentId: string, text: string) =>
  call<{ comment: TeacherComment }>("", { method: "PUT", body: JSON.stringify({ kind, ref, student_id: studentId, text }) }).then(
    (r) => r.comment,
  );
export const deleteComment = (commentId: string) => call<{ ok: boolean }>(`/${encodeURIComponent(commentId)}`, { method: "DELETE" });
