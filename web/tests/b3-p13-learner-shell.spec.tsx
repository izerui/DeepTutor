/**
 * B3-P13: a learning account's shell must not request what the learner
 * surface denies (kb-registry task-17 C1). The backend keeps returning 403 for
 * these routes; the shell simply stops asking, and non-learner accounts keep
 * the old requests unchanged.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: "en" } }),
}));
vi.mock("next/link", () => ({
  default: ({
    children,
    href,
    prefetch: _prefetch,
    ...rest
  }: {
    children: React.ReactNode;
    href: string;
    prefetch?: boolean;
  }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

const LEARNER_STATUS = {
  enabled: true,
  authenticated: true,
  user_id: "u_learner",
  username: "learner",
  role: "student",
  preset: "learner",
  learning_policy: {
    age_band: "9-12",
    locked_persona: "teacher",
    allowed_capabilities: ["chat", "immersive_reading", "mastery_path"],
    default_capability: "immersive_reading",
    allowed_surfaces: ["chat", "reading"],
  },
};

const STANDARD_STATUS = {
  enabled: true,
  authenticated: true,
  user_id: "u_standard",
  username: "standard",
  role: "user",
  preset: "standard",
  learning_policy: null,
};

/** Path prefixes the backend learner surface denies (auth.py require_learning_surface). */
const DENIED = [
  "/api/system/update",
  "/api/settings/workspace/registrations",
  "/api/settings/workspace/resources",
  "/api/capabilities/registered",
  "/api/tools",
  "/api/subagents/",
  "/api/partners",
  "/api/partner-groups",
  "/api/dashboard/suggestions",
  "/api/settings/chat-attachments",
];

function pathOf(input: RequestInfo | URL): string {
  const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  return new URL(raw, "http://localhost/").pathname;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

let requested: string[] = [];

function installFetch(status: typeof LEARNER_STATUS | typeof STANDARD_STATUS) {
  requested = [];
  const learner = status.learning_policy !== null;
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const path = pathOf(input);
    requested.push(path);
    if (path === "/api/auth/status") return json(status);
    if (path === "/api/settings/llm-options") return json({ options: [], default: null });
    if (learner) return json({ detail: "This learning account cannot use the requested server surface." }, 403);
    if (path === "/api/settings") return json({ catalog: {}, ui: {} });
    if (path === "/api/settings/workspace/registrations") return json({ workspaces: [] });
    if (path === "/api/settings/workspace/resources")
      return json({ skills: [], mcp: [], knowledge_bases: [] });
    if (path === "/api/capabilities/registered") return json({ capabilities: [] });
    if (path === "/api/tools") return json({ enabled_optional_tools: [] });
    if (path === "/api/subagents/connections") return json({ connections: [] });
    if (path === "/api/subagents/settings") return json({ consult_budget: 2, backends: {} });
    if (path === "/api/partners" || path === "/api/partner-groups") return json([]);
    if (path === "/api/settings/chat-attachments")
      return json({ effective: { max_file_bytes: 1024, max_total_bytes: 2048 } });
    if (path === "/api/dashboard/suggestions") return json({ suggestions: [], stale: false, status: "ready" });
    if (path === "/api/system/update")
      return json({
        current_version: "1.0.0",
        check_enabled: true,
        checked_at: "",
        cached: true,
        check_error: "",
        update_available: false,
        release: null,
        installation: { mode: "source", automatic_update: false, command: "", reason: "" },
        launcher_managed: false,
        is_admin: false,
        job: null,
      });
    return json({});
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function deniedRequests(): string[] {
  return requested.filter((path) => DENIED.some((prefix) => path === prefix || path.startsWith(prefix)));
}

type Rtl = typeof import("@testing-library/react");
let rtl: Rtl;

beforeEach(async () => {
  // Each test gets fresh module caches (auth status, client cache, voice prefs).
  // Testing Library is re-imported with them so it shares the fresh React.
  vi.resetModules();
  rtl = await import("@testing-library/react");
});

afterEach(() => {
  rtl.cleanup();
  vi.unstubAllGlobals();
});

async function flush() {
  const { act } = rtl;
  await act(async () => {
    for (let i = 0; i < 10; i += 1) await Promise.resolve();
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function exerciseShellData() {
  const { listWorkspaces, getWorkspaceResources } = await import("@/lib/workspaces-api");
  const { fetchCapabilityCatalog } = await import("@/features/capabilities/api");
  const { getEnabledOptionalTools } = await import("@/lib/tools-settings");
  const { listSubagentConnections, getSubagentSettings } = await import("@/lib/subagents-api");
  const { listPartners } = await import("@/lib/partners-api");
  const { listPartnerGroups } = await import("@/lib/partner-groups-api");
  return Promise.allSettled([
    listWorkspaces(true),
    getWorkspaceResources(""),
    fetchCapabilityCatalog({ force: true }),
    getEnabledOptionalTools({ force: true }),
    listSubagentConnections(),
    getSubagentSettings({ force: true }),
    listPartners(),
    listPartnerGroups(),
  ]);
}

describe("B3-P13 learner shell", () => {
  it("VersionBadge does not check for updates or show a failure for a learner", async () => {
    const { render, screen } = rtl;
    installFetch(LEARNER_STATUS);
    const { VersionBadge } = await import("@/components/sidebar/VersionBadge");
    render(<VersionBadge />);
    await flush();
    await flush();
    expect(deniedRequests()).toEqual([]);
    expect(requested).toContain("/api/auth/status");
    expect(requested).not.toContain("/api/system/update");
    expect(screen.queryByLabelText("Status check failed")).toBeNull();
    expect(screen.queryByText("Status check failed")).toBeNull();
  });

  it("shell data clients do not request denied routes for a learner", async () => {
    installFetch(LEARNER_STATUS);
    const results = await exerciseShellData();
    // Neutral, empty answers rather than surfaced 403 errors.
    for (const result of results.slice(0, 5)) expect(result.status).toBe("fulfilled");
    expect(results[6].status).toBe("fulfilled");
    expect(results[7].status).toBe("fulfilled");
    expect(deniedRequests()).toEqual([]);
  });

  it("settings-backed shell probes skip GET /api/settings for a learner", async () => {
    const { renderHook, waitFor } = rtl;
    installFetch(LEARNER_STATUS);
    const { CapabilityAccessProvider, useCapabilityAccess } = await import(
      "@/components/access/CapabilityAccessContext"
    );
    const { useVoiceAutoplayPreference } = await import("@/hooks/useVoiceAutoplay");
    const { useAttachmentLimits, DEFAULT_ATTACHMENT_LIMITS } = await import("@/lib/attachment-limits");
    const { useVoiceMathSpeakPreference } = await import("@/hooks/useVoiceMathSpeak");
    const access = renderHook(() => useCapabilityAccess(), {
      wrapper: ({ children }) => <CapabilityAccessProvider>{children}</CapabilityAccessProvider>,
    });
    const autoplay = renderHook(() => useVoiceAutoplayPreference());
    const mathSpeak = renderHook(() => useVoiceMathSpeakPreference());
    const limits = renderHook(() => useAttachmentLimits());
    await waitFor(() => expect(access.result.current.loading).toBe(false));
    await waitFor(() => expect(autoplay.result.current.loading).toBe(false));
    await waitFor(() => expect(mathSpeak.result.current.loading).toBe(false));
    expect(access.result.current.isAdmin).toBe(false);
    expect(autoplay.result.current.value).toBe(false);
    expect(mathSpeak.result.current.value).toBe(true);
    expect(limits.result.current).toEqual(DEFAULT_ATTACHMENT_LIMITS);
    expect(requested).not.toContain("/api/settings");
    expect(deniedRequests()).toEqual([]);
    expect(requested).toContain("/api/settings/llm-options");
  });

  it("StarterSuggestions renders nothing and does not ask for suggestions for a learner", async () => {
    const { render } = rtl;
    installFetch(LEARNER_STATUS);
    const { default: StarterSuggestions } = await import("@/components/chat/home/StarterSuggestions");
    const { container } = render(<StarterSuggestions onPick={() => undefined} workspaceId="" />);
    await flush();
    await flush();
    expect(deniedRequests()).toEqual([]);
    expect(requested).toContain("/api/auth/status");
    expect(deniedRequests()).toEqual([]);
    expect(container).toBeEmptyDOMElement();
  });

  it("a standard account keeps every shell request unchanged", async () => {
    const { act, render, renderHook, screen } = rtl;
    installFetch(STANDARD_STATUS);
    const results = await exerciseShellData();
    for (const result of results) expect(result.status).toBe("fulfilled");
    const { VersionBadge } = await import("@/components/sidebar/VersionBadge");
    const { default: StarterSuggestions } = await import("@/components/chat/home/StarterSuggestions");
    const { CapabilityAccessProvider, useCapabilityAccess } = await import(
      "@/components/access/CapabilityAccessContext"
    );
    const { useAttachmentLimits } = await import("@/lib/attachment-limits");
    let access!: ReturnType<typeof renderHook<ReturnType<typeof useCapabilityAccess>, unknown>>;
    await act(async () => {
      render(<VersionBadge />);
      render(<StarterSuggestions onPick={() => undefined} workspaceId="" />);
      renderHook(() => useAttachmentLimits());
      access = renderHook(() => useCapabilityAccess(), {
        wrapper: ({ children }) => <CapabilityAccessProvider>{children}</CapabilityAccessProvider>,
      });
    });
    await flush();
    await flush();
    expect(access.result.current.loading).toBe(false);
    for (const path of [
      "/api/system/update",
      "/api/dashboard/suggestions",
      "/api/settings",
      "/api/settings/workspace/registrations",
      "/api/settings/workspace/resources",
      "/api/capabilities/registered",
      "/api/tools",
      "/api/subagents/connections",
      "/api/subagents/settings",
      "/api/partners",
      "/api/partner-groups",
      "/api/settings/chat-attachments",
    ]) {
      expect(requested).toContain(path);
    }
    expect(access.result.current.isAdmin).toBe(true);
    expect(screen.getByLabelText("Not checked yet")).toBeInTheDocument();
  });
});
