import { act, render, screen } from "@testing-library/react";
import { createRef } from "react";
import { expect, it, vi } from "vitest";

import SessionViewerPanel, {
  type SessionViewerPanelHandle,
} from "@/components/chat/home/SessionViewerPanel";
import { initI18n } from "@/i18n/init";
import { buildSessionActivity } from "@/lib/session-activity";
import type { SelectionTutorContext } from "@/lib/selection-tutor";

vi.mock("@/components/tasks/TaskPanelSection", () => ({ default: () => null }));
vi.mock("@/components/quiz/QuizFollowupTabBody", () => ({
  __esModule: true,
  default: ({ context }: { context: { followupSessionId: string | null } }) => (
    <div data-testid="tutor-body">{context.followupSessionId ?? "fresh"}</div>
  ),
}));

initI18n("en");

function thread(id: string, sourceMessageId = 7): SelectionTutorContext {
  return {
    scope: "message",
    selectedText: "",
    parentSessionId: "chat",
    sourceMessageId,
    sourceMessageText: "",
    sourceMessageRole: "assistant",
    threadSessionId: id,
  };
}

function renderPanel() {
  const ref = createRef<SessionViewerPanelHandle>();
  const onAutoOpen = vi.fn();
  const onTabsChange = vi.fn();
  render(
    <SessionViewerPanel
      ref={ref}
      open
      sessionId="chat"
      activity={buildSessionActivity([])}
      onClose={() => {}}
      onAutoOpen={onAutoOpen}
      onTabsChange={onTabsChange}
    />,
  );
  return { ref, onAutoOpen, onTabsChange };
}

it("restores tutor tabs in the background and focuses the saved one", async () => {
  const { ref, onAutoOpen, onTabsChange } = renderPanel();
  onTabsChange.mockClear();

  act(() => ref.current!.restoreTutorTabs([thread("t1"), thread("t2", 8)], "en", "t2"));

  expect(onAutoOpen).not.toHaveBeenCalled();
  expect(onTabsChange).toHaveBeenCalled();
  expect((await screen.findByTestId("tutor-body")).textContent).toBe("t2");
  expect(ref.current!.openTutorThreads()).toEqual({ threadIds: ["t1", "t2"], activeThreadId: "t2" });
});

it("does not duplicate an open thread or take focus from the user's tab", async () => {
  const { ref } = renderPanel();
  act(() => ref.current!.openSelectionTutorTab(thread("t1"), "en"));
  expect((await screen.findByTestId("tutor-body")).textContent).toBe("t1");

  act(() => ref.current!.restoreTutorTabs([thread("t1"), thread("t2", 8)], "en", "t2"));

  expect(screen.getByTestId("tutor-body").textContent).toBe("t1");
  expect(ref.current!.openTutorThreads()).toEqual({ threadIds: ["t1", "t2"], activeThreadId: "t1" });
});

it("leaves a tutor tab without a server thread out of the saved list", async () => {
  const { ref } = renderPanel();
  act(() =>
    ref.current!.openSelectionTutorTab({ ...thread("t1"), threadSessionId: null }, "en"),
  );
  expect((await screen.findByTestId("tutor-body")).textContent).toBe("fresh");
  expect(ref.current!.openTutorThreads()).toEqual({ threadIds: [], activeThreadId: null });
});
