import { useEffect } from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import QueuedMessages from "@/features/chat/components/QueuedMessages";
import {
  ChatStateAdapterProvider,
  readUserInputIds,
  useChatStateAdapter,
} from "@/features/chat/ChatStateAdapter";
import { initI18n } from "@/i18n/init";

initI18n("en");

// The real adapter and transport: what matters is what reaches the socket
// while a turn runs, and what happens to a message the turn never read.
class TestSocket extends EventTarget {
  static instances: TestSocket[] = [];
  readyState = 0;
  sent: Record<string, unknown>[] = [];

  constructor() {
    super();
    TestSocket.instances.push(this);
  }

  open() {
    this.readyState = 1;
    this.dispatchEvent(new Event("open"));
  }

  send(data: string) {
    this.sent.push(JSON.parse(data));
  }

  close() {
    this.readyState = 3;
    this.dispatchEvent(new Event("close"));
  }

  frame(seq: number, type: string, metadata = {}, content = "") {
    this.dispatchEvent(
      new MessageEvent("message", {
        data: JSON.stringify({
          protocol_version: "2.0",
          type,
          turn_id: "turn-1",
          session_id: "session-1",
          seq,
          timestamp: Date.now() / 1000,
          metadata,
          content,
        }),
      }),
    );
  }
}

function Harness() {
  const chat = useChatStateAdapter();
  useEffect(() => {
    chat.newSession();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return (
    <>
      <button onClick={() => chat.sendMessage("Explain FFT")}>Start turn</button>
      <button
        onClick={() =>
          chat.queueMessage("Only the discrete case.", ["Only the discrete case."], true)
        }
      >
        Queue
      </button>
      <span data-testid="streaming">{String(chat.state.isStreaming)}</span>
      <QueuedMessages
        messages={chat.state.queuedMessages}
        isStreaming={chat.state.isStreaming}
        onWithdraw={chat.withdrawQueuedMessage}
      />
    </>
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  TestSocket.instances = [];
});

async function startTurn() {
  vi.stubGlobal("WebSocket", TestSocket);
  const user = userEvent.setup();
  render(
    <ChatStateAdapterProvider>
      <Harness />
    </ChatStateAdapterProvider>,
  );
  await user.click(screen.getByRole("button", { name: "Start turn" }));
  const socket = TestSocket.instances.at(-1)!;
  act(() => socket.open());
  await waitFor(() => expect(socket.sent.some((m) => m.type === "start_turn")).toBe(true));
  act(() => {
    socket.frame(1, "session");
    socket.frame(2, "content", { call_kind: "agent_loop_round" }, "Searching.");
  });
  return { socket, user };
}

const offers = (socket: TestSocket) => socket.sent.filter((m) => m.type === "user_input");

it("offers a message sent mid-turn to the running turn and drops it once read", async () => {
  const { socket, user } = await startTurn();
  await user.click(screen.getByRole("button", { name: "Queue" }));

  await waitFor(() => expect(offers(socket)).toHaveLength(1));
  const offer = offers(socket)[0];
  expect(offer).toMatchObject({
    turn_id: "turn-1",
    session_id: "session-1",
    content: "Only the discrete case.",
  });
  expect(screen.getByText("Only the discrete case.")).toBeInTheDocument();
  expect(screen.getByText("Read after this step")).toBeInTheDocument();

  // The loop read it: the row the turn wrote retires it from the queue.
  act(() =>
    socket.frame(3, "progress", {
      trace_kind: "user_input",
      user_inputs: [{ command_id: offer.command_id, content: "Only the discrete case." }],
    }),
  );
  await waitFor(() => expect(screen.queryByText("Only the discrete case.")).toBeNull());
});

it("takes an unread message back from the running turn", async () => {
  const { socket, user } = await startTurn();
  await user.click(screen.getByRole("button", { name: "Queue" }));
  await waitFor(() => expect(offers(socket)).toHaveLength(1));
  const offered = offers(socket)[0];

  await user.click(screen.getByRole("button", { name: "Remove from queue" }));

  await waitFor(() => expect(offers(socket)).toHaveLength(2));
  expect(offers(socket)[1]).toMatchObject({
    turn_id: "turn-1",
    withdraw: offered.command_id,
    content: "",
  });
  expect(offers(socket)[1].command_id).not.toBe(offered.command_id);
  expect(screen.queryByText("Only the discrete case.")).toBeNull();
});

it("sends what the turn never read as the next turn once it ends", async () => {
  const { socket, user } = await startTurn();
  await user.click(screen.getByRole("button", { name: "Queue" }));
  await waitFor(() => expect(offers(socket)).toHaveLength(1));
  const starts = () =>
    TestSocket.instances.flatMap((s) => s.sent).filter((m) => m.type === "start_turn");
  expect(starts()).toHaveLength(1);

  act(() => socket.frame(3, "done", { status: "completed" }));

  // The next turn starts on a connection of its own.
  await waitFor(() => expect(TestSocket.instances.length).toBeGreaterThan(1));
  act(() => TestSocket.instances.at(-1)!.open());
  await waitFor(() => expect(starts()).toHaveLength(2));
  expect(starts()[1]).toMatchObject({ content: "Only the discrete case." });
});

it("reads which queued messages a user_input row says were read", () => {
  expect(
    readUserInputIds({
      type: "progress",
      source: "chat",
      stage: "",
      content: "",
      timestamp: 0,
      metadata: { trace_kind: "user_input", user_inputs: [{ command_id: "a" }, {}] },
    }),
  ).toEqual(["a"]);
  expect(
    readUserInputIds({
      type: "progress",
      source: "chat",
      stage: "",
      content: "",
      timestamp: 0,
      metadata: { trace_kind: "call_status" },
    }),
  ).toEqual([]);
});
