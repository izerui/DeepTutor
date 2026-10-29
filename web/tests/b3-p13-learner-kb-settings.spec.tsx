/**
 * B3-P13 follow-up: the knowledge-base page and the settings surface must not
 * request routes the learner surface denies (auth.py require_learning_surface):
 * GET /api/knowledge-bases/rag-providers and /supported-file-types (not in
 * _LEARNER_KB_READ_ROUTES), GET /api/settings, GET /api/settings/draft and
 * GET /api/system/status. GET /api/settings/ui is on the public router and is
 * NOT denied, so the app shell keeps reading it for learners too. Settings
 * stays reachable for the sections a learner can use (language, appearance,
 * learner profile, archived chats); everything else is hidden and redirected.
 *
 * The real learner gate (@/lib/learning-shell) is exercised: only the HTTP
 * layer is mocked, and /api/auth/status carries the learning_policy.
 */
import { act, cleanup, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const fixture = vi.hoisted(() => ({
  learner: false,
  calls: [] as string[],
  replace: vi.fn(),
}));

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const LEARNER_POLICY = {
  age_band: "9-12",
  locked_persona: "teacher",
  allowed_capabilities: ["chat"],
  default_capability: "chat",
  allowed_surfaces: ["chat", "reading"],
};

vi.mock("@/lib/api", () => ({
  apiUrl: (path: string) => path,
  setRuntimeAuthEnabled: () => undefined,
  apiFetch: vi.fn(async (url: string) => {
    const path = url.split("?")[0];
    fixture.calls.push(path);
    if (path === "/api/auth/status")
      return json({
        enabled: true,
        authenticated: true,
        user_id: "u1",
        role: "user",
        preset: fixture.learner ? "learner" : "standard",
        learning_policy: fixture.learner ? LEARNER_POLICY : null,
      });
    // Public router: answered for everyone, learners included.
    if (path === "/api/settings/ui") return json({ language: "uk", response_language: "uk" });
    if (fixture.learner && path !== "/api/knowledge-bases" && path !== "/api/knowledge-bases/list")
      return json({ detail: "denied" }, 403);
    if (path === "/api/knowledge-bases" || path === "/api/knowledge-bases/list") return json([]);
    if (path === "/api/knowledge-bases/rag-providers")
      return json({ providers: [{ id: "llamaindex", name: "LlamaIndex", description: "" }] });
    if (path === "/api/knowledge-bases/supported-file-types") return json({});
    if (path === "/api/settings")
      return json({ ui: { theme: "dark", language: "en", response_language: "en" } });
    if (path === "/api/settings/draft") return json({ draft: null });
    return json({});
  }),
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: "en" } }),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: fixture.replace }),
  usePathname: () => "/settings/tools",
}));

beforeEach(async () => {
  fixture.calls = [];
  fixture.replace.mockReset();
  localStorage.clear();
  const { invalidateAuthStatusCache } = await import("@/lib/auth");
  const { invalidateClientCache } = await import("@/lib/client-cache");
  invalidateAuthStatusCache();
  invalidateClientCache("");
});

afterEach(() => cleanup());

const DENIED_KB = ["/api/knowledge-bases/rag-providers", "/api/knowledge-bases/supported-file-types"];
const DENIED_SETTINGS = ["/api/settings", "/api/settings/draft", "/api/system/status"];

describe("B3-P13 knowledge-base page", () => {
  it("a learner loads knowledge bases without provider or upload-policy requests", async () => {
    fixture.learner = true;
    const { useKnowledgeBases } = await import("@/hooks/useKnowledgeBases");
    const { result } = renderHook(() => useKnowledgeBases());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBeNull();
    expect(result.current.providers).toEqual([]);
    expect(fixture.calls.filter((path) => DENIED_KB.includes(path))).toEqual([]);
  });

  it("a standard account still reads providers and the upload policy", async () => {
    fixture.learner = false;
    const { useKnowledgeBases } = await import("@/hooks/useKnowledgeBases");
    const { result } = renderHook(() => useKnowledgeBases());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.providers.map((p) => p.id)).toEqual(["llamaindex"]);
    for (const path of DENIED_KB) expect(fixture.calls).toContain(path);
  });

  it("a learner is a read-only knowledge-base viewer", async () => {
    fixture.learner = true;
    const { useAuthStatus } = await import("@/hooks/useAuthStatus");
    const learner = renderHook(() => useAuthStatus());
    await waitFor(() => expect(learner.result.current.loading).toBe(false));
    expect(learner.result.current.allowPrivateKb).toBe(false);

    fixture.learner = false;
    const { invalidateAuthStatusCache } = await import("@/lib/auth");
    invalidateAuthStatusCache();
    const standard = renderHook(() => useAuthStatus());
    await waitFor(() => expect(standard.result.current.loading).toBe(false));
    expect(standard.result.current.allowPrivateKb).toBe(true);
  });
});

describe("B3-P13 settings surface", () => {
  it("lists only the learner-usable settings pages", async () => {
    const { settingsAccessFromAuthStatus } = await import(
      "@/features/settings/navigation/settings-access"
    );
    const { visibleSettingsPages } = await import("@/features/settings/navigation/settings-pages");
    const base = { enabled: true, authenticated: true, is_admin: false, role: "user" };
    const learnerKeys = visibleSettingsPages(
      settingsAccessFromAuthStatus({ ...base, preset: "learner", learning_policy: LEARNER_POLICY }),
    ).map((page) => page.key);
    expect(new Set(learnerKeys)).toEqual(
      new Set(["general", "appearance", "learner-profile", "archive"]),
    );
    const standardKeys = visibleSettingsPages(
      settingsAccessFromAuthStatus({ ...base, preset: "standard", learning_policy: null }),
    ).map((page) => page.key);
    expect(standardKeys).toEqual(expect.arrayContaining(["general", "tools", "about"]));
    // Spec 2026-10-29 D4: workspace management is for administrators only.
    expect(standardKeys).not.toContain("workspace");
    expect(standardKeys).not.toContain("data-migration");
    expect(standardKeys).not.toContain("learner-profile");
  });

  it("a learner's settings store reads local preferences instead of denied routes", async () => {
    fixture.learner = true;
    localStorage.setItem("deeptutor-language", "fr");
    vi.doMock("@/context/AppShellContext", () => ({
      useAppShell: () => ({
        codeBlockTheme: "github",
        codeBlockShowLineNumbers: true,
        codeBlockWrapLongLines: false,
        setCodeBlockTheme: vi.fn(),
        setCodeBlockShowLineNumbers: vi.fn(),
        setCodeBlockWrapLongLines: vi.fn(),
      }),
    }));
    const { SettingsProvider, useSettings } = await import("@/features/settings/store/SettingsStore");
    const { result } = renderHook(() => useSettings(), {
      wrapper: ({ children }) => <SettingsProvider>{children}</SettingsProvider>,
    });
    await waitFor(() => expect(result.current.settingsLoading).toBe(false));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(result.current.settingsError).toBeNull();
    expect(result.current.catalogEditable).toBe(false);
    expect(result.current.language).toBe("fr");
    expect(fixture.calls.filter((path) => DENIED_SETTINGS.includes(path))).toEqual([]);
    vi.doUnmock("@/context/AppShellContext");
  });

  it("redirects a learner away from a settings page they cannot use", async () => {
    vi.doMock("@/features/settings/navigation/SettingsAccessProvider", () => ({
      useSettingsAccess: () => ({
        resolved: true,
        hideAdminOnly: true,
        showLearnerOnly: true,
        showGuardianOnly: false,
        learningShell: true,
      }),
    }));
    vi.doMock("@/features/settings/store/SettingsStore", () => ({
      useSettings: () => ({ settingsLoading: false, saving: false, applying: false }),
    }));
    const { default: SettingsPageContent } = await import("@/components/settings/SettingsPageContent");
    render(<SettingsPageContent section="tools" />);
    await waitFor(() => expect(fixture.replace).toHaveBeenCalledWith("/settings/general"));
    expect(screen.queryByText("This settings page is unavailable.")).toBeNull();
    vi.doUnmock("@/features/settings/navigation/SettingsAccessProvider");
    vi.doUnmock("@/features/settings/store/SettingsStore");
  });

  it("the app shell still adopts the public saved UI language for a learner", async () => {
    fixture.learner = true;
    vi.doUnmock("@/context/AppShellContext");
    const { AppShellProvider, useAppShell } = await import("@/context/AppShellContext");
    function Probe() {
      const { language, languageReady } = useAppShell();
      return <output data-testid="language" data-ready={languageReady}>{language}</output>;
    }
    render(
      <AppShellProvider>
        <Probe />
      </AppShellProvider>,
    );
    await waitFor(() =>
      expect(screen.getByTestId("language")).toHaveAttribute("data-ready", "true"),
    );
    expect(fixture.calls).toContain("/api/settings/ui");
    expect(screen.getByTestId("language")).toHaveTextContent("uk");
    // No auth-status round-trip in front of it: the shell asks exactly as before.
    expect(fixture.calls).not.toContain("/api/auth/status");
  });
});
