import type { AuthStatus } from "@/lib/auth";
import { isLearningShellStatus } from "@/lib/learning-shell";

export interface SettingsAccess {
  /** False until the backend has resolved the runtime auth mode and account. */
  resolved: boolean;
  /** Admin-owned settings stay hidden on auth failures and for ordinary users. */
  hideAdminOnly: boolean;
  /** The self-service learner profile belongs only to learner accounts. */
  showLearnerOnly: boolean;
  /** Ordinary standard/custom accounts may act as authorized guardians. */
  showGuardianOnly: boolean;
  /**
   * A learning policy is in effect: the backend default-denies every settings
   * route except the caller's own UI preferences, so only the pages that work
   * on that surface are offered (B3-P13). Absent means no learning policy.
   */
  learningShell?: boolean;
}

export const PENDING_SETTINGS_ACCESS: SettingsAccess = {
  resolved: false,
  hideAdminOnly: true,
  showLearnerOnly: false,
  showGuardianOnly: false,
  learningShell: false,
};

/** Convert the backend's account identity into the settings visibility model. */
export function settingsAccessFromAuthStatus(
  authStatus: AuthStatus | null,
): SettingsAccess {
  if (!authStatus) {
    return { ...PENDING_SETTINGS_ACCESS, resolved: true };
  }

  const ordinaryAuthenticatedUser = Boolean(
    authStatus.enabled && authStatus.authenticated && !authStatus.is_admin,
  );
  return {
    resolved: true,
    hideAdminOnly: Boolean(authStatus.enabled) && !authStatus.is_admin,
    showLearnerOnly:
      ordinaryAuthenticatedUser && authStatus.preset === "learner",
    // Spec 2026-10-29 D16: guardians are retired.
    showGuardianOnly: false,
    learningShell: isLearningShellStatus(authStatus),
  };
}
