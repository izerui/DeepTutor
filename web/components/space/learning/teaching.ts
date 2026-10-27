import type { MasteryTopic } from "@/lib/learning-api";
import {
  isTeachingApiError,
  type ChangeKind,
  type ChangeSuggestion,
  type KpDecision,
  type PublishBlockedDetail,
  type PublishPreview,
  type SourceIssue,
  type TeacherTemplate,
} from "@/lib/teaching-api";

export function isTeacherInstance(topic: Pick<MasteryTopic, "origin">): boolean {
  return topic.origin?.kind === "teacher_template";
}

export function splitAssignedTopics<T extends Pick<MasteryTopic, "origin">>(
  topics: T[],
): { assigned: T[]; own: T[] } {
  const assigned: T[] = [];
  const own: T[] = [];
  for (const topic of topics) (isTeacherInstance(topic) ? assigned : own).push(topic);
  return { assigned, own };
}

/** The live template whose draft is this topic, in this topic's workspace. */
export function templateForTopic(
  templates: TeacherTemplate[],
  topic: Pick<MasteryTopic, "path_id" | "content_workspace_id">,
): TeacherTemplate | undefined {
  const workspace = topic.content_workspace_id ?? "";
  return templates.find(
    (template) =>
      template.status !== "archived" &&
      template.draft_path_id === topic.path_id &&
      template.draft_workspace_id === workspace,
  );
}

/** One `<select>` value per decision: `kind` or `kind:from_id`. */
export function decisionValue(decision: KpDecision | undefined): string {
  if (!decision) return "unchanged";
  return decision.from_id ? `${decision.kind}:${decision.from_id}` : decision.kind;
}

export function parseDecision(value: string): KpDecision {
  const split = value.indexOf(":");
  if (split < 0) return { kind: value as ChangeKind };
  return { kind: value.slice(0, split) as ChangeKind, from_id: value.slice(split + 1) };
}

export function defaultKpDecisions(items: ChangeSuggestion[]): Record<string, KpDecision> {
  return Object.fromEntries(
    items.map((item) => [
      item.kp_id,
      item.from_id ? { kind: item.suggested, from_id: item.from_id } : { kind: item.suggested },
    ]),
  );
}

/** Removed points that more than one new point claims to continue. */
export function duplicateContinuations(decisions: Record<string, KpDecision>): string[] {
  const counts = new Map<string, number>();
  for (const decision of Object.values(decisions)) {
    if (decision.from_id) counts.set(decision.from_id, (counts.get(decision.from_id) ?? 0) + 1);
  }
  return [...counts].filter(([, count]) => count > 1).map(([kpId]) => kpId);
}

export function canSubmitPublish(
  preview: PublishPreview | null,
  decisions: Record<string, KpDecision>,
  busy: boolean,
): boolean {
  return Boolean(preview?.can_publish) && !busy && duplicateContinuations(decisions).length === 0;
}

export { withMasteryTemplate } from "@/lib/mastery-template-scope";

// ---- presentation helpers for the teacher dialogs ---------------------------

/** A catalog key plus its interpolation values; the dialogs call `t(key, values)`. */
export interface Message {
  key: string;
  values?: Record<string, string | number>;
}

/** Server source-issue codes, localized; the server's English text is never shown as the reason. */
const ISSUE_KEYS: Record<string, string> = {
  personal_source:
    "{{label}} is a personal source and cannot be shared with students. Upload it to a knowledge base and reference that instead.",
  connector:
    "{{label}} is a personal connector and cannot be shared. Upload the material to a knowledge base instead.",
  missing_kb: "{{label}}: the knowledge base no longer exists.",
  not_shareable: "{{label}} is someone else's private knowledge base.",
  not_indexed: "{{label}} has not finished indexing.",
  missing_document: "{{label}}: the document no longer exists in its knowledge base.",
  missing_book: "{{label}}: the book no longer exists.",
  workspace_unavailable: "{{label}}: the workspace it came from is unavailable.",
  book_kb_blocked: "{{label}}: a knowledge base this book cites cannot be shared with students.",
};
const BOOK_NOT_SHAREABLE = "{{label}} is someone else's private book, or cannot be shared.";
const UNKNOWN_ISSUE = "{{label}} cannot be shared with students.";

/** `sourceKinds` maps the draft's source ids to their kinds (a book vs. a library). */
export function sourceIssueMessage(
  issue: SourceIssue,
  sourceKinds: Record<string, string> = {},
): Message {
  const values = { label: issue.label };
  if (issue.code === "not_shareable" && sourceKinds[issue.source_id] === "book") {
    return { key: BOOK_NOT_SHAREABLE, values };
  }
  if (issue.code === "publish_step_failed") {
    return { key: "Publishing stopped; no new version was published.", values };
  }
  return { key: ISSUE_KEYS[issue.code] ?? UNKNOWN_ISSUE, values };
}

/** Steps reported by a failed publish (`failed_step`) or in post-version `warnings`. */
export function publishStepMessage(
  step: string,
  names: Record<string, string> = {},
): Message {
  const [action, kind, ...rest] = step.split(":");
  const id = rest.join(":");
  const name = names[id] || id;
  if (action === "make_public" && kind === "kb") {
    return { key: "Making the knowledge base {{name}} public", values: { name } };
  }
  if (action === "make_public" && kind === "book") {
    return { key: "Making the book {{name}} public", values: { name } };
  }
  if (action === "share" && kind === "book") {
    return { key: "Sharing the knowledge bases of {{name}}", values: { name } };
  }
  const fixed: Record<string, string> = {
    documents: "Registering the cited documents",
    knowledge_base_references: "Knowledge base access for students",
    book_references: "Book access for students",
    assignments: "Student assignments",
  };
  return fixed[step] ? { key: fixed[step] } : { key: "Step {{step}}", values: { step } };
}

/** The structured body of a 422 PublishBlocked answer, when that is what failed. */
export function publishBlocked(error: unknown): PublishBlockedDetail | null {
  if (!isTeachingApiError(error) || error.status !== 422) return null;
  const detail = error.detail;
  if (typeof detail !== "object" || detail === null) return null;
  const outline = (detail as { outline_issues?: unknown }).outline_issues;
  const sources = (detail as { source_issues?: unknown }).source_issues;
  if (!Array.isArray(outline) || !Array.isArray(sources)) return null;
  return detail as unknown as PublishBlockedDetail;
}

/**
 * Teacher-route failures other than PublishBlocked, localized by HTTP status.
 * These answers carry English sentences only, so the status is what decides.
 */
export function teachingErrorMessage(
  error: unknown,
  context: "publish" | "manage" = "publish",
): Message {
  if (!isTeachingApiError(error)) return { key: "Update failed" };
  switch (error.status) {
    case 403:
      return context === "manage"
        ? { key: "You can no longer manage this teacher path." }
        : { key: "Only the teacher who owns this path can publish it." };
    case 404:
      return { key: "This teacher path or assignment no longer exists." };
    case 409:
      return {
        key: "This teacher path changed in the meantime (edited, published or archived elsewhere). Load it again and retry.",
      };
    case 422:
      // A plain sentence is the service's student check; request validation
      // (a list, e.g. too many ids) is not about student accounts.
      return typeof error.detail === "string"
        ? { key: "Only active student accounts can be assigned." }
        : { key: "Update failed" };
    default:
      return { key: "Update failed" };
  }
}

/** Why a template has no publish control on this page, if it has none. */
export function unpublishableReason(template: TeacherTemplate): Message {
  if (!template.is_owner) {
    return {
      key: "Only {{owner}} can edit and publish this path. You can still manage its students or archive it.",
      values: { owner: template.owner_name || "?" },
    };
  }
  if (!template.draft_path_id) {
    return {
      key: "Its teacher's account was removed, so it has no editable draft and cannot be published again. You can still manage its students or archive it.",
    };
  }
  return {
    key: "Its draft path is not in your library, so it cannot be published from here. You can still manage its students or archive it.",
  };
}

/**
 * Live templates without a card of their own on the atlas: another teacher's
 * (seen by an admin), a deleted teacher's transferred path, or one whose
 * draft is gone. They are managed from a list instead of a card.
 */
export function unlistedTemplates(
  templates: TeacherTemplate[],
  topics: Pick<MasteryTopic, "path_id" | "content_workspace_id">[],
): TeacherTemplate[] {
  return templates.filter(
    (template) =>
      template.status !== "archived" &&
      !(
        template.is_owner &&
        topics.some((topic) => templateForTopic([template], topic) === template)
      ),
  );
}
