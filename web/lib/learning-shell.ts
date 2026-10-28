import { fetchAuthStatus, type AuthStatus } from "@/lib/auth";

/**
 * Whether an auth status describes a learning account (one with an effective
 * ``learning_policy``). The backend default-denies every route outside that
 * policy's surfaces (``require_learning_surface``), so the shell must not ask
 * for settings, workspaces, partners, tools, subagents, update checks and the
 * like on its behalf: the answer is always 403 (B3-P13).
 */
export function isLearningShellStatus(
  status: AuthStatus | null | undefined,
): boolean {
  return Boolean(status?.learning_policy);
}

/**
 * Resolve whether the current account runs the learning shell.
 *
 * Shares the single-flight ``/api/auth/status`` read the rest of the shell
 * already makes. An unreachable or failing status read resolves ``false``: nothing is
 * withheld and the backend stays the enforcement boundary.
 */
export async function learningShellActive(): Promise<boolean> {
  try {
    return isLearningShellStatus(await fetchAuthStatus());
  } catch {
    return false;
  }
}
