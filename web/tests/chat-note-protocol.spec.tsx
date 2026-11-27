import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import {
  ChatStateAdapterProvider,
  useChatStateAdapter,
} from "@/features/chat/ChatStateAdapter";
import { readFailedSubmissions } from "@/lib/failed-submissions";
import { updateRecoveryPrincipal } from "@/lib/recovery-principal";
import { buildStartTurnInput } from "@/features/chat/controllers/buildStartTurnInput";
import { initI18n } from "@/i18n/init";

initI18n("en");

const fixture = vi.hoisted(() => ({
  requests: [] as Record<string, unknown>[],
  emit: (_event: Record<string, unknown>) => {},
  getSession: vi.fn(),
}));

vi.mock("@/lib/session-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/session-api")>()),
  getSession: fixture.getSession,
}));

vi.mock("@/features/chat/transport/UnifiedTurnClient", () => ({
  UnifiedTurnClient: class {
    connected = true;
    constructor(emit: typeof fixture.emit) { fixture.emit = emit; }
    connect() {}
    disconnect() {}
    setResumeState() {}
    send(message: Record<string, unknown>) { fixture.requests.push(message); }
  },
}));

const pageId = "0123456789abcdef";
const target = { block_id: "block-quiz", kind: "quiz" };
const starts = () => fixture.requests.filter((request) => request.type === "start_turn");
const mount = () => renderHook(useChatStateAdapter, { wrapper: ChatStateAdapterProvider });
const serverSession = (snapshot: Record<string, unknown> = {}) => ({
  id: "session-1",
  session_id: "session-1",
  title: "Note companion",
  status: "completed",
  created_at: 1,
  updated_at: 1,
  preferences: { note_document_id: pageId },
  messages: [{
    id: 1, session_id: "session-1", role: "user", content: "Make a quiz",
    events: [], attachments: [], created_at: 1,
    metadata: { request_snapshot: {
      content: "Make a quiz", enabledTools: [], knowledgeBases: [], language: "en",
      ...snapshot,
    } },
  }],
});

function emit(type: string, seq: number, metadata = {}) {
  act(() => fixture.emit({
    type, seq, session_id: "session-1", turn_id: "turn-1",
    timestamp: Date.now() / 1000, metadata,
  }));
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  updateRecoveryPrincipal({ enabled: true, authenticated: true, user_id: "note-protocol-test" });
  fixture.requests.length = 0;
  fixture.getSession.mockReset().mockResolvedValue(serverSession());
});

it("sends the companion binding and generation target and retains the draft identity after admission", async () => {
  const { result } = mount();
  act(() => { result.current.newSession({ noteDocumentId: pageId }); });
  const draftKey = result.current.state.sessionKey;
  await act(async () => {
    await result.current.sendMessage("Make a quiz", undefined, undefined, undefined, undefined,
      { noteGeneration: target });
  });
  expect(starts()[0]).toMatchObject({ note_document_id: pageId, note_generation: target });
  expect(result.current.state.messages[0].requestSnapshot?.noteGeneration).toEqual(target);
  expect(readFailedSubmissions("draft:general")[0].requestSnapshot).toMatchObject({
    noteGeneration: target, submissionRequest: { note_document_id: pageId, note_generation: target },
  });
  emit("session", 1);
  expect(result.current.state).toMatchObject({
    sessionKey: "session-1", sessionId: "session-1", boundFromKey: draftKey, noteDocumentId: pageId,
  });
  emit("session", 2);
  expect(result.current.state.boundFromKey).toBe(draftKey);
});

it("replays the exact unsaved draft after reload even when the visible note binding changes", async () => {
  const first = mount();
  act(() => { first.result.current.newSession({ noteDocumentId: pageId }); });
  await act(async () => {
    await first.result.current.sendMessage("Make a quiz", undefined, undefined, undefined, undefined,
      { noteGeneration: target });
  });
  const original = { ...starts()[0] };
  first.unmount();
  const restored = mount();
  expect(restored.result.current.state.messages[0].requestSnapshot?.noteGeneration).toEqual(target);
  act(() => restored.result.current.configureSession({ noteDocumentId: "fedcba9876543210" }));
  const message = restored.result.current.state.messages[0];
  await act(async () => {
    expect(await restored.result.current.retryFailedSubmission(message.id!)).toBe(true);
  });
  expect(starts()[1]).toEqual(original);
});

it.each(["noteGeneration", "note_generation"])(
  "restores %s from a persisted snapshot and keeps it when editing the message",
  async (field) => {
    fixture.getSession.mockResolvedValue(serverSession({ [field]: target }));
    const { result } = mount();
    await act(async () => { await result.current.loadSession("session-1"); });
    expect(result.current.state.noteDocumentId).toBe(pageId);
    expect(result.current.state.messages[0].requestSnapshot?.noteGeneration).toEqual(target);
    await act(async () => { expect(await result.current.editMessage(1, "Make a harder quiz")).toBe(true); });
    expect(starts()[0]).toMatchObject({
      content: "Make a harder quiz", note_document_id: pageId, note_generation: target,
    });
  },
);

it("keeps queued generation targets for the next turn and does not inherit them on ordinary sends", async () => {
  const { result } = mount();
  act(() => { result.current.newSession({ noteDocumentId: pageId }); });
  await act(async () => { await result.current.sendMessage("Explain this page"); });
  emit("session", 1);
  act(() => result.current.queueMessage("Make a quiz",
    ["Make a quiz", undefined, undefined, undefined, undefined, { noteGeneration: target }], false));
  expect(starts()).toHaveLength(1);
  emit("done", 2, { status: "completed" });
  await waitFor(() => expect(starts()).toHaveLength(2));
  expect(starts()[1]).toMatchObject({ note_document_id: pageId, note_generation: target });
  act(() => { result.current.newSession(); });
  expect(result.current.state).toMatchObject({ boundFromKey: null, noteDocumentId: null });
  await act(async () => { await result.current.sendMessage("Ordinary chat"); });
  expect(starts()[2]).not.toHaveProperty("note_document_id");
  expect(starts()[2]).not.toHaveProperty("note_generation");
});

it("clears local configuration and hydrates absent bindings without inventing a generation target", async () => {
  const { result } = mount();
  act(() => result.current.configureSession({ noteDocumentId: pageId }));
  act(() => result.current.configureSession({ noteDocumentId: null }));
  expect(result.current.state.noteDocumentId).toBeNull();
  fixture.getSession.mockResolvedValue({
    ...serverSession({ note_generation: { block_id: 123, kind: "quiz" } }),
    preferences: { note_document_id: "" },
  });
  await act(async () => { await result.current.loadSession("session-1"); });
  expect(result.current.state.noteDocumentId).toBeNull();
  expect(result.current.state.messages[0].requestSnapshot).not.toHaveProperty("noteGeneration");
  const plain = buildStartTurnInput({ content: "Hello" });
  expect(plain).not.toHaveProperty("note_document_id");
  expect(plain).not.toHaveProperty("note_generation");
});
