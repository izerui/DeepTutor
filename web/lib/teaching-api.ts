import { apiFetch, apiUrl } from "./api";

/** Teacher templates are system-level: these routes are never workspace-scoped. */
const BASE = "/api/mastery-paths/teaching";

export type TemplateStatus = "draft" | "published" | "archived";

export interface TeacherTemplate {
  id: string;
  owner_user_id: string;
  owner_name: string;
  is_owner: boolean;
  title: string;
  description: string;
  status: TemplateStatus;
  current_revision: number;
  draft_path_id: string;
  draft_workspace_id: string;
  assigned_count: number;
  /** Labels of referenced materials that can no longer be resolved. */
  materials_unavailable: string[];
  created_at: number;
  updated_at: number;
}

export interface SourceIssue {
  source_id: string;
  label: string;
  code: string;
  message: string;
}

/** The structured detail body of a 422 PublishBlocked response. */
export interface PublishBlockedDetail {
  outline_issues: string[];
  source_issues: SourceIssue[];
  failed_step?: string;
  completed_steps?: string[];
}

/**
 * Typed error thrown by teaching API calls. Preserves the HTTP status and
 * the parsed `detail` payload so callers (C14/C15) can extract structured
 * fields (outline issues, source issues, failed step) for localization.
 *
 * Follows the BookApiError pattern used elsewhere in the codebase.
 */
export class TeachingApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly detail: string | Record<string, unknown>,
  ) {
    super(message);
    this.name = "TeachingApiError";
  }
}

export function isTeachingApiError(error: unknown): error is TeachingApiError {
  return error instanceof TeachingApiError;
}

export type KbChoice = "make_public" | "reference_only";

export type ChangeKind = "unchanged" | "reworded" | "redefined" | "added";

export interface ChangeSuggestion {
  kp_id: string;
  name: string;
  status: "kept" | "new";
  suggested: ChangeKind;
  from_id: string;
  from_name: string;
  reason: string;
}

/** A private KB of the teacher that the path, or one of its books (`via`), cites. */
export interface PrivateKbChoice {
  kb_id: string;
  name: string;
  default: KbChoice;
  via: string;
  via_name: string;
}

export interface PrivateBookChoice {
  book_id: string;
  name: string;
  default: KbChoice;
}

export interface PublishWarning {
  step: string;
  message: string;
}

export interface PublishPreview {
  template: TeacherTemplate;
  draft_revision: number;
  outline_issues: string[];
  source_issues: SourceIssue[];
  private_kbs: PrivateKbChoice[];
  private_books: PrivateBookChoice[];
  books: { book_id: string; title: string }[];
  changes: {
    first_publish: boolean;
    items: ChangeSuggestion[];
    removed: { kp_id: string; name: string }[];
  };
  module_count: number;
  knowledge_point_count: number;
  affected_students: number;
  can_publish: boolean;
}

export interface KpDecision {
  kind: ChangeKind;
  from_id?: string;
}

export interface PublishInput {
  draft_revision: number;
  kp_decisions: Record<string, KpDecision>;
  kb_choices: Record<string, KbChoice>;
  book_choices: Record<string, KbChoice>;
  student_ids: string[];
}

export interface StudentOption {
  id: string;
  username: string;
  avatar: string;
}

export interface TemplateAssignment {
  template_id: string;
  student_user_id: string;
  username: string;
  avatar: string;
  status: "active" | "revoked";
  instance_path_id: string;
  applied_revision: number;
  assigned_at: number;
  revoked_at: number | null;
}

async function teachingJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await apiFetch(apiUrl(`${BASE}${path}`), init);
  if (!res.ok) {
    let message = `${res.status}`;
    let detail: string | Record<string, unknown> = message;
    try {
      const payload = (await res.json()) as { detail?: unknown };
      if (typeof payload.detail === "string") {
        message = payload.detail;
        detail = payload.detail;
      } else if (payload.detail && typeof payload.detail === "object") {
        detail = payload.detail as Record<string, unknown>;
        message = JSON.stringify(detail);
      }
    } catch {
      // Keep the status when a proxy answers with HTML.
    }
    throw new TeachingApiError(message, res.status, detail);
  }
  return res.json() as Promise<T>;
}

function jsonInit(method: string, body?: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  };
}

const id = encodeURIComponent;

export async function listTeacherTemplates(): Promise<TeacherTemplate[]> {
  const result = await teachingJson<{ templates: TeacherTemplate[] }>("/templates", {
    cache: "no-store",
  });
  return Array.isArray(result.templates) ? result.templates : [];
}

export function registerTeacherTemplate(pathId: string, workspaceId: string) {
  return teachingJson<TeacherTemplate>(
    "/templates",
    jsonInit("POST", { path_id: pathId, workspace_id: workspaceId }),
  );
}

export function previewPublish(templateId: string) {
  return teachingJson<PublishPreview>(
    `/templates/${id(templateId)}/publish/preview`,
    jsonInit("POST"),
  );
}

export function publishTemplate(templateId: string, input: PublishInput) {
  return teachingJson<{
    template: TeacherTemplate;
    revision: number;
    affected_students: number;
    warnings: PublishWarning[];
  }>(
    `/templates/${id(templateId)}/publish`,
    jsonInit("POST", input),
  );
}

export async function listAssignments(templateId: string): Promise<TemplateAssignment[]> {
  const result = await teachingJson<{ assignments: TemplateAssignment[] }>(
    `/templates/${id(templateId)}/assignments`,
    { cache: "no-store" },
  );
  return result.assignments;
}

export async function assignStudents(
  templateId: string,
  studentIds: string[],
): Promise<TemplateAssignment[]> {
  const result = await teachingJson<{ assignments: TemplateAssignment[] }>(
    `/templates/${id(templateId)}/assignments`,
    jsonInit("POST", { student_ids: studentIds }),
  );
  return result.assignments;
}

export function revokeAssignment(templateId: string, studentId: string) {
  return teachingJson<{ ok: boolean }>(
    `/templates/${id(templateId)}/assignments/${id(studentId)}`,
    { method: "DELETE" },
  );
}

export function archiveTemplate(templateId: string) {
  return teachingJson<TeacherTemplate>(`/templates/${id(templateId)}/archive`, jsonInit("POST"));
}

export function deleteTemplate(templateId: string) {
  return teachingJson<{ ok: boolean }>(`/templates/${id(templateId)}`, { method: "DELETE" });
}

export async function searchStudents(query: string): Promise<StudentOption[]> {
  const result = await teachingJson<{ students: StudentOption[] }>(
    `/students?q=${encodeURIComponent(query)}`,
    { cache: "no-store" },
  );
  return result.students;
}
