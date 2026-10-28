/**
 * T15-M5 red test: switching from path A to path B must immediately clear the
 * topic so the provider never exposes A's template_id while B's fetch is
 * in-flight.
 *
 * The fix in useMasteryStudySession.ts is a single `setTopic(null)` at the
 * top of the pathId effect.
 */
import { renderHook, act, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

// ── Deferred promise helper ────────────────────────────────────
function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

// ── Mocks ──────────────────────────────────────────────────────
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

const stableT = (k: string) => k;
const stableI18n = { language: "en" };
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: stableT, i18n: stableI18n }),
}));

const mockNewSession = vi.fn(() => "draft-key");
vi.mock("@/features/chat/ChatStateAdapter", () => ({
  useChatStateAdapter: () => ({
    state: {
      sessionId: null,
      isStreaming: false,
      messages: [],
      knowledgeBases: [],
      llmSelection: null,
      personaSelection: "",
      workspaceId: "",
      resourceSelection: null,
      selectedBranches: {},
    },
    newSession: mockNewSession,
    configureSession: vi.fn(),
    loadSession: vi.fn(),
    showCachedSession: vi.fn(),
  }),
}));

vi.mock("@/hooks/useMasteryPathActivity", () => ({
  useMasteryPathActivity: () => ({
    revision: 0,
    unavailable: false,
    connection: "live",
    refresh: vi.fn(),
  }),
}));

const mockFetchTopic = vi.fn(() => new Promise(() => {}));
vi.mock("@/lib/learning-api", () => ({
  fetchMasteryTopic: (...args: unknown[]) => mockFetchTopic(...args),
  fetchMasteryTopicSessions: vi.fn(() => Promise.resolve([])),
  isCourseUnavailable: () => false,
}));

vi.mock("@/lib/mastery-study-route", () => ({
  isMasteryDraftSessionReady: () => false,
  isMasteryDraftSendReady: () => false,
}));

vi.mock("@/lib/course-session-scope", () => ({
  courseSessionConfiguration: (cfg: unknown) => cfg,
}));

vi.mock("@/lib/learning-routes", () => ({
  masterySessionRoute: (pathId: string) => `/learning/mastery/${pathId}/sessions`,
}));

import { useMasteryStudySession } from "@/hooks/useMasteryStudySession";

function minimalTopic(pathId: string) {
  return {
    path_id: pathId,
    name: `Topic ${pathId}`,
    metadata: {},
    sources: [],
    path_revision: 1,
    next: { step: "study" },
    map: { modules: [], objectives: [] },
    reviews: [],
    learner_profile: null,
    session_count: 0,
    updated_at: Date.now(),
    template_id: `template-${pathId}`,
  };
}

describe("T15-M5 topic cleared on path switch", () => {
  beforeEach(() => {
    // Reset but keep a safe default that returns a never-resolving promise
    // so that unexpected calls don't throw.
    mockFetchTopic.mockReset();
    mockFetchTopic.mockImplementation(() => new Promise(() => {}));
    mockNewSession.mockClear();
  });

  it("nulls topic immediately when pathId changes, before the new fetch resolves", async () => {
    // Path A: use a deferred so we can control resolution timing precisely.
    const topicA = minimalTopic("path-a");
    const aDeferred = deferred<ReturnType<typeof minimalTopic>>();
    mockFetchTopic.mockImplementationOnce(() => aDeferred.promise);

    const { result, rerender } = renderHook(
      ({ pathId }: { pathId: string }) => useMasteryStudySession(pathId),
      { initialProps: { pathId: "path-a" } },
    );

    // Verify the mock was called for path A.
    expect(mockFetchTopic).toHaveBeenCalledWith("path-a", expect.anything());
    // Before A resolves, topic is null.
    expect(result.current.topic).toBeNull();

    // Resolve A's topic.
    await act(async () => {
      aDeferred.resolve(topicA);
    });
    expect(result.current.topic?.path_id).toBe("path-a");

    // Path B: use a deferred promise so we control when it resolves.
    const bDeferred = deferred<ReturnType<typeof minimalTopic>>();
    mockFetchTopic.mockImplementationOnce(() => bDeferred.promise);

    // Switch to path B.
    act(() => {
      rerender({ pathId: "path-b" });
    });

    // Immediately after the switch, topic must be null — not A's.
    // The fix is `setTopic(null)` at the top of the effect.
    expect(result.current.topic).toBeNull();

    // Resolve B and verify it loads.
    const topicB = minimalTopic("path-b");
    await act(async () => {
      bDeferred.resolve(topicB);
    });
    expect(result.current.topic?.path_id).toBe("path-b");
  });
});
