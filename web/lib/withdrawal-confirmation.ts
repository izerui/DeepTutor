/**
 * Rule R1: an owner may always make a knowledge base or book private, or
 * delete it. When published teacher paths cite it, the server first answers
 * 409 `referenced_by_teacher_paths` with aggregate counts only; the client
 * shows them and repeats the request with `confirm: true`.
 */

export const WITHDRAWAL_CONFIRMATION_CODE = "referenced_by_teacher_paths";

export interface WithdrawalImpact {
  affectedPaths: number;
  affectedStudents: number;
}

/** Other accounts' references, which the server only ever reports as counts. */
export interface OtherReferences {
  total: number;
  by_kind: Record<string, number>;
}

export const NO_OTHER_REFERENCES: OtherReferences = { total: 0, by_kind: {} };

type Translate = (key: string, options?: Record<string, unknown>) => string;

function count(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? Math.floor(value) : 0;
}

/** The impact a 409 `detail` object announces, or `null` for any other error. */
export function parseWithdrawalImpact(detail: unknown): WithdrawalImpact | null {
  if (!detail || typeof detail !== "object") return null;
  const raw = detail as Record<string, unknown>;
  if (raw.code !== WITHDRAWAL_CONFIRMATION_CODE) return null;
  return { affectedPaths: count(raw.affected_paths), affectedStudents: count(raw.affected_students) };
}

export function parseOtherReferences(value: unknown): OtherReferences {
  if (!value || typeof value !== "object") return NO_OTHER_REFERENCES;
  const raw = value as Record<string, unknown>;
  const byKind: Record<string, number> = {};
  if (raw.by_kind && typeof raw.by_kind === "object") {
    for (const [kind, n] of Object.entries(raw.by_kind as Record<string, unknown>)) byKind[kind] = count(n);
  }
  return { total: count(raw.total), by_kind: byKind };
}

/** Request fields confirming exactly the counts the owner saw (server re-checks them). */
export function confirmationFields(impact: WithdrawalImpact | null | undefined): {
  confirm?: true;
  expected_paths?: number;
  expected_students?: number;
} {
  return impact
    ? { confirm: true, expected_paths: impact.affectedPaths, expected_students: impact.affectedStudents }
    : {};
}

/** Thrown by API helpers when the server asks to confirm a withdrawal. */
export class WithdrawalConfirmationError extends Error {
  constructor(
    message: string,
    readonly impact: WithdrawalImpact,
  ) {
    super(message);
    this.name = "WithdrawalConfirmationError";
  }
}

/** The confirmation question, in counts only (never whose paths or students). */
export function describeWithdrawalImpact(impact: WithdrawalImpact, t: Translate): string {
  return [
    t("{{count}} published teacher paths use this material", { count: impact.affectedPaths }),
    t("{{count}} students assigned", { count: impact.affectedStudents }),
    t("Those paths will show this material as unavailable. Continue?"),
  ].join("\n");
}

/**
 * Run a withdrawal (make private / delete); when published teacher paths need
 * consent, ask with the counts and, if accepted, run it again confirming those
 * counts. If they changed meanwhile the server answers with fresh counts,
 * which are asked about again (a few times at most). Other errors propagate.
 */
export async function runWithWithdrawalConfirmation(
  run: (confirmed: WithdrawalImpact | null) => Promise<void>,
  t: Translate,
  ask: (question: string) => boolean = (question) => window.confirm(question),
): Promise<boolean> {
  let confirmed: WithdrawalImpact | null = null;
  for (let round = 0; round < 4; round += 1) {
    try {
      await run(confirmed);
      return true;
    } catch (error) {
      if (!(error instanceof WithdrawalConfirmationError) || round === 3) throw error;
      if (!ask(describeWithdrawalImpact(error.impact, t))) return false;
      confirmed = error.impact;
    }
  }
  return false;
}

/** One line about other accounts' references, or `""` when there are none. */
export function describeOtherReferences(others: OtherReferences, t: Translate): string {
  return others.total > 0
    ? t("{{count}} items from other accounts use this material and will show it as unavailable.", {
        count: others.total,
      })
    : "";
}
