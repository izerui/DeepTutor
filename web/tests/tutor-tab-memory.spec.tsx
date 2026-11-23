import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SessionViewerPanelHandle } from "@/components/chat/home/SessionViewerPanel";
import { useTutorTabMemory } from "@/hooks/useTutorTabMemory";
import { updateRecoveryPrincipal } from "@/lib/recovery-principal";
import type { SessionSummary } from "@/lib/session-api";
import type { SelectionTutorContext } from "@/lib/selection-tutor";
import {
  readTutorTabMemory,
  tutorTabMemoryKey,
  writeTutorTabMemory,
} from "@/lib/tutor-tab-memory";

function thread(id: string): SessionSummary {
  return {
    session_id: id,
    preferences: { parent_session_id: "chat", tutor_scope: "message", tutor_source_message_id: "7" },
  } as unknown as SessionSummary;
}

function selectionFor(row: SessionSummary): SelectionTutorContext {
  return {
    scope: "message",
    selectedText: "",
    parentSessionId: "chat",
    sourceMessageId: 7,
    sourceMessageText: "",
    sourceMessageRole: "assistant",
    threadSessionId: row.session_id,
  };
}

function fakePanel(open: { threadIds: string[]; activeThreadId: string | null } | null) {
  const panel = {
    restoreTutorTabs: vi.fn(),
    openTutorThreads: vi.fn(() => open),
  };
  return { panel, ref: { current: panel as unknown as SessionViewerPanelHandle } };
}

function signIn(userId: string) {
  act(() => updateRecoveryPrincipal({ enabled: true, authenticated: true, user_id: userId }));
}

beforeEach(() => {
  window.localStorage.clear();
  signIn("alice");
});

afterEach(() => {
  act(() => updateRecoveryPrincipal(null));
});

describe("tutor tab memory storage", () => {
  it("has no key before the account is known", () => {
    act(() => updateRecoveryPrincipal(null));
    expect(tutorTabMemoryKey("chat")).toBeNull();
  });

  it("keeps each account's tabs apart", () => {
    const aliceKey = tutorTabMemoryKey("chat")!;
    writeTutorTabMemory(aliceKey, { threadIds: ["t1"], activeThreadId: "t1" });
    signIn("bob");
    const bobKey = tutorTabMemoryKey("chat")!;
    expect(bobKey).not.toBe(aliceKey);
    expect(readTutorTabMemory(bobKey)).toBeNull();
  });

  it("ignores malformed data and forgets an empty tab list", () => {
    const key = tutorTabMemoryKey("chat")!;
    window.localStorage.setItem(key, "{not json");
    expect(readTutorTabMemory(key)).toBeNull();
    writeTutorTabMemory(key, { threadIds: ["t1", "t2"], activeThreadId: "gone" });
    expect(readTutorTabMemory(key)).toEqual({ threadIds: ["t1", "t2"], activeThreadId: null });
    writeTutorTabMemory(key, { threadIds: [], activeThreadId: null });
    expect(readTutorTabMemory(key)).toBeNull();
  });
});

describe("useTutorTabMemory", () => {
  it("restores only threads the server still lists, then saves the result", () => {
    const key = tutorTabMemoryKey("chat")!;
    writeTutorTabMemory(key, { threadIds: ["deleted", "t2", "t1"], activeThreadId: "t1" });
    const { panel, ref } = fakePanel({ threadIds: ["t2", "t1"], activeThreadId: "t1" });

    renderHook(() =>
      useTutorTabMemory({
        sessionId: "chat",
        threads: [thread("t1"), thread("t2")],
        threadsLoaded: true,
        language: "zh",
        panelRef: ref,
        selectionForThread: selectionFor,
      }),
    );

    expect(panel.restoreTutorTabs).toHaveBeenCalledTimes(1);
    const [selections, language, active] = panel.restoreTutorTabs.mock.calls[0];
    expect(selections.map((s: SelectionTutorContext) => s.threadSessionId)).toEqual(["t2", "t1"]);
    expect(language).toBe("zh");
    expect(active).toBe("t1");
    expect(readTutorTabMemory(key)).toEqual({ threadIds: ["t2", "t1"], activeThreadId: "t1" });
  });

  it("does not overwrite saved tabs before the thread list has loaded", () => {
    const key = tutorTabMemoryKey("chat")!;
    writeTutorTabMemory(key, { threadIds: ["t1"], activeThreadId: null });
    const { panel, ref } = fakePanel({ threadIds: [], activeThreadId: null });

    const { result } = renderHook(() =>
      useTutorTabMemory({
        sessionId: "chat",
        threads: [],
        threadsLoaded: false,
        language: "en",
        panelRef: ref,
        selectionForThread: selectionFor,
      }),
    );
    act(() => result.current());

    expect(panel.restoreTutorTabs).not.toHaveBeenCalled();
    expect(readTutorTabMemory(key)).toEqual({ threadIds: ["t1"], activeThreadId: null });
  });

  it("restores again for a conversation it returns to, without touching the other", () => {
    const chatKey = tutorTabMemoryKey("chat")!;
    const otherKey = tutorTabMemoryKey("other")!;
    writeTutorTabMemory(chatKey, { threadIds: ["t1"], activeThreadId: null });
    writeTutorTabMemory(otherKey, { threadIds: ["o1"], activeThreadId: null });
    const { panel, ref } = fakePanel(null);

    const { rerender } = renderHook(
      ({ sessionId, threads }: { sessionId: string; threads: SessionSummary[] }) =>
        useTutorTabMemory({
          sessionId,
          threads,
          threadsLoaded: true,
          language: "en",
          panelRef: ref,
          selectionForThread: selectionFor,
        }),
      { initialProps: { sessionId: "chat", threads: [thread("t1")] } },
    );
    rerender({ sessionId: "other", threads: [thread("o1")] });
    rerender({ sessionId: "chat", threads: [thread("t1")] });

    expect(panel.restoreTutorTabs).toHaveBeenCalledTimes(3);
    // The panel had not loaded (null snapshot): nothing was overwritten.
    expect(readTutorTabMemory(chatKey)?.threadIds).toEqual(["t1"]);
    expect(readTutorTabMemory(otherKey)?.threadIds).toEqual(["o1"]);
  });
});
