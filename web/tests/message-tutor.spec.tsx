// Little Tutor from a message (spec 2026-11-20-message-tutor): the reply's
// "Ask Little Tutor" action, the thread line under a message, and "Back to
// source" from inside a thread.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ChatMessageList } from "@/features/chat/messages";
import { initI18n } from "@/i18n/init";
import type { SessionSummary } from "@/lib/session-api";
import {
  QuizFollowupProvider,
  type QuizFollowupTabContext,
} from "@/context/QuizFollowupContext";
import QuizFollowupTabBody from "@/components/quiz/QuizFollowupTabBody";

vi.mock("@/components/quiz/FollowupChatComposer", () => ({
  default: () => null,
}));
vi.mock("@/lib/session-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/session-api")>()),
  getSession: vi.fn(async () => ({ messages: [] })),
}));

initI18n("en");
afterEach(cleanup);

const conversation = [
  { id: 7, role: "user" as const, content: "Explain paging", parentMessageId: null },
  {
    id: 8,
    role: "assistant" as const,
    content: "Paging maps virtual pages to physical frames.",
    parentMessageId: 7,
  },
];

function thread(id: string, source: number): SessionSummary {
  return {
    id,
    session_id: id,
    title: `Thread ${id}`,
    created_at: 1,
    updated_at: 1,
    message_count: 2,
    last_message: "",
    preferences: {
      session_kind: "selection_tutor",
      tutor_scope: "message",
      tutor_source_message_id: source,
    },
  };
}

describe("message-level Little Tutor in the chat", () => {
  it("asks about a whole reply by its id", () => {
    const onAskTutor = vi.fn();
    render(
      <ChatMessageList
        messages={conversation}
        isStreaming={false}
        sessionId="parent"
        onCopyAssistantMessage={vi.fn()}
        onRegenerateMessage={vi.fn()}
        onAskTutor={onAskTutor}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Ask Little Tutor" }));
    expect(onAskTutor).toHaveBeenCalledWith(8);
  });

  it("offers no tutor action while the reply is still streaming", () => {
    render(
      <ChatMessageList
        messages={conversation}
        isStreaming
        sessionId="parent"
        onCopyAssistantMessage={vi.fn()}
        onRegenerateMessage={vi.fn()}
        onAskTutor={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: "Ask Little Tutor" })).toBeNull();
  });

  it("shows the thread line under the message it was asked from", () => {
    const onOpenThread = vi.fn();
    const { container } = render(
      <ChatMessageList
        messages={conversation}
        isStreaming={false}
        sessionId="parent"
        onCopyAssistantMessage={vi.fn()}
        onRegenerateMessage={vi.fn()}
        threadsByMessageId={new Map([["8", [thread("t1", 8), thread("t2", 8)]]])}
        onOpenThread={onOpenThread}
        onDeleteThread={vi.fn(async () => {})}
      />,
    );
    const reply = container.querySelector('[data-chat-message-id="8"]');
    const question = container.querySelector('[data-chat-message-id="7"]');
    expect(reply?.querySelector("[data-message-thread-marker]")).toHaveTextContent(
      "Little Tutor · 2",
    );
    expect(question?.querySelector("[data-message-thread-marker]")).toBeNull();

    fireEvent.click(
      screen.getByRole("button", { name: "Little Tutor threads on this message" }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Open Little Tutor thread: Thread t1" }),
    );
    expect(onOpenThread).toHaveBeenCalledWith(
      expect.objectContaining({ session_id: "t1" }),
    );
  });
});

function tutorTab(
  overrides: Partial<NonNullable<QuizFollowupTabContext["tutorSelection"]>> = {},
): QuizFollowupTabContext {
  return {
    questionKey: "selection-tutor:thread:t1",
    question: {
      question_id: "q",
      question: "",
      question_type: "concept",
      correct_answer: "",
      explanation: "",
    },
    userAnswer: "",
    isCorrect: null,
    answerImages: [],
    aiJudgment: "",
    parentQuizSessionId: null,
    notebookEntryId: null,
    followupSessionId: null,
    language: "en",
    tabLabel: "Little Tutor",
    tutorSelection: {
      scope: "message",
      selectedText: "",
      parentSessionId: "parent",
      sourceMessageId: 8,
      sourceMessageText: "Paging maps virtual pages to physical frames.",
      sourceMessageRole: "assistant",
      ...overrides,
    },
  };
}

function renderTab(context: QuizFollowupTabContext) {
  return render(
    <QuizFollowupProvider>
      <QuizFollowupTabBody context={context} />
    </QuizFollowupProvider>,
  );
}

describe("Back to source", () => {
  it("scrolls to and highlights the source message", () => {
    const source = document.createElement("div");
    source.setAttribute("data-chat-message-id", "8");
    const scrollIntoView = vi.fn();
    source.scrollIntoView = scrollIntoView;
    document.body.appendChild(source);
    try {
      renderTab(tutorTab());
      expect(screen.getByText("About this reply")).toBeTruthy();
      fireEvent.click(screen.getByRole("button", { name: "Back to source" }));
      expect(scrollIntoView).toHaveBeenCalled();
      expect(source.getAttribute("data-tutor-source-highlight")).toBe("true");
      expect(
        screen.queryByText("The original message is no longer in this conversation"),
      ).toBeNull();
    } finally {
      source.remove();
    }
  });

  it("says so when the message is not in the current conversation", () => {
    renderTab(tutorTab({ sourceMessageId: 99 }));
    fireEvent.click(screen.getByRole("button", { name: "Back to source" }));
    expect(
      screen.getByText("The original message is no longer in this conversation"),
    ).toBeTruthy();
  });

  it("marks a thread whose source message was deleted", () => {
    renderTab(tutorTab({ sourceMessageId: null, sourceDeleted: true }));
    expect(screen.getByText("Original message deleted")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Back to source" })).toBeNull();
  });
});
