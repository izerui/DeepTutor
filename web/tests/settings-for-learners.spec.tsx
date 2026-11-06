/**
 * Spec 2026-11-06: settings for learners, and no more "learning restrictions".
 *
 * - A non-admin sees only the personal settings pages (the "everyone" rows of
 *   the spec table); an admin sees every page.
 * - A non-admin opening an admin page lands on /settings/general.
 * - The admin "Add user" dialog no longer offers "Learning restrictions".
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { settingsAccessFromAuthStatus } from "@/features/settings/navigation/settings-access";
import { visibleSettingsPages } from "@/features/settings/navigation/settings-pages";

const fixture = vi.hoisted(() => ({
  replace: vi.fn(),
  access: { resolved: true, hideAdminOnly: true, showLearnerOnly: true },
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: "en" } }),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: fixture.replace }),
  usePathname: () => "/settings/memory",
}));

afterEach(() => {
  cleanup();
  fixture.replace.mockReset();
});

const LEARNER_PAGES = [
  "general",
  "appearance",
  "learner-profile",
  "archive",
  "progress",
  "usage",
  "starters",
  "tools",
  "about",
];

const ADMIN_ONLY_PAGES = [
  "connections",
  "llm",
  "task-models",
  "embedding",
  "search",
  "voice",
  "multimodal",
  "knowledge",
  "network",
  "status",
  "memory",
  "capabilities",
  "attachments",
  "video-learning",
  "workspace",
  "data-migration",
];

const learner = settingsAccessFromAuthStatus({
  enabled: true,
  authenticated: true,
  is_admin: false,
  role: "learner",
  // A legacy restricted account reads the same as any other learner.
  preset: "learner",
});
const admin = settingsAccessFromAuthStatus({
  enabled: true,
  authenticated: true,
  is_admin: true,
  role: "admin",
  preset: "standard",
});

describe("settings page set", () => {
  it("shows a learner exactly the personal pages", () => {
    const keys = visibleSettingsPages(learner).map((page) => page.key);
    expect(new Set(keys)).toEqual(new Set(LEARNER_PAGES));
  });

  it("shows an admin every admin page, but not the learner profile", () => {
    const keys = visibleSettingsPages(admin).map((page) => page.key);
    expect(keys).toEqual(expect.arrayContaining([...ADMIN_ONLY_PAGES, "general", "about", "tools"]));
    expect(keys).not.toContain("learner-profile");
    expect(keys).not.toContain("guardian");
  });

  it("gives a local single-user (auth off) run every page", () => {
    const local = settingsAccessFromAuthStatus({ enabled: false, authenticated: true, is_admin: true });
    expect(visibleSettingsPages(local).map((page) => page.key)).toEqual(
      expect.arrayContaining(ADMIN_ONLY_PAGES),
    );
  });
});

describe("settings page routing", () => {
  it("sends a learner who opens an admin page to General", async () => {
    vi.doMock("@/features/settings/navigation/SettingsAccessProvider", () => ({
      useSettingsAccess: () => fixture.access,
    }));
    vi.doMock("@/features/settings/store/SettingsStore", () => ({
      useSettings: () => ({ settingsLoading: false, saving: false, applying: false }),
    }));
    const { default: SettingsPageContent } = await import("@/components/settings/SettingsPageContent");
    for (const section of ["memory", "capabilities", "llm"]) {
      fixture.replace.mockReset();
      const view = render(<SettingsPageContent section={section} />);
      await waitFor(() => expect(fixture.replace).toHaveBeenCalledWith("/settings/general"));
      expect(screen.queryByText("This settings page is unavailable.")).toBeNull();
      view.unmount();
    }
    vi.doUnmock("@/features/settings/navigation/SettingsAccessProvider");
    vi.doUnmock("@/features/settings/store/SettingsStore");
  });
});

describe("admin Add user dialog", () => {
  it("creates a learner without a Learning restrictions option", async () => {
    const createUser = vi.fn().mockResolvedValue({ ok: true });
    vi.doMock("@/lib/auth", () => ({
      fetchAuthStatus: vi.fn().mockResolvedValue({
        enabled: true,
        authenticated: true,
        role: "admin",
        is_admin: true,
        username: "root",
      }),
    }));
    vi.doMock("@/lib/admin-api", () => ({
      listUsers: vi.fn().mockResolvedValue([]),
      deleteUsers: vi.fn(),
      deleteUser: vi.fn(),
      resetUserPassword: vi.fn(),
      setUserRole: vi.fn(),
      importUsers: vi.fn(),
      createUser,
    }));
    vi.doMock("@/components/admin/RegistrationToggle", () => ({ RegistrationToggle: () => null }));
    vi.doMock("@/components/catalog/AdminNav", () => ({ AdminNav: () => null }));
    vi.doMock("@/features/multi-user/components/MigrationResultsNotice", () => ({
      MigrationResultsNotice: () => null,
    }));
    const { default: AdminUsersPage } = await import("@/app/(admin)/admin/users/page");
    render(<AdminUsersPage />);
    fireEvent.click((await screen.findAllByRole("button", { name: "Add user" }))[0]);
    expect(await screen.findByText("Password (≥ 8 chars)")).toBeTruthy();
    expect(screen.queryByText("Learning restrictions")).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
    vi.doUnmock("@/lib/auth");
    vi.doUnmock("@/lib/admin-api");
    vi.doUnmock("@/components/admin/RegistrationToggle");
    vi.doUnmock("@/components/catalog/AdminNav");
    vi.doUnmock("@/features/multi-user/components/MigrationResultsNotice");
  });
});
