import type { MasteryTopic } from "@/lib/learning-api";
import type {
  ChangeKind,
  ChangeSuggestion,
  KpDecision,
  PublishPreview,
  TeacherTemplate,
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
