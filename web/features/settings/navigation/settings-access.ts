import type { AuthStatus } from "@/lib/auth";

export interface SettingsAccess {
  /** False until the backend has resolved the runtime auth mode and account. */
  resolved: boolean;
  /**
   * Admin-owned settings stay hidden on auth failures and for learners: a
   * learner sees only the personal pages (spec 2026-11-06).
   */
  hideAdminOnly: boolean;
  /** The self-service learner profile belongs to every signed-in non-admin. */
  showLearnerOnly: boolean;
}

export const PENDING_SETTINGS_ACCESS: SettingsAccess = {
  resolved: false,
  hideAdminOnly: true,
  showLearnerOnly: false,
};

/** Convert the backend's account identity into the settings visibility model. */
export function settingsAccessFromAuthStatus(
  authStatus: AuthStatus | null,
): SettingsAccess {
  if (!authStatus) {
    return { ...PENDING_SETTINGS_ACCESS, resolved: true };
  }

  return {
    resolved: true,
    hideAdminOnly: Boolean(authStatus.enabled) && !authStatus.is_admin,
    showLearnerOnly: Boolean(
      authStatus.enabled && authStatus.authenticated && !authStatus.is_admin,
    ),
  };
}
