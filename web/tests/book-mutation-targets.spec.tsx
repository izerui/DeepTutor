import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BookDetail, Block, Spine } from "@/lib/book-types";
import type { PageReaderProps } from "@/app/(workspace)/learning/books/components/PageReader";
import type { BookSocketLike, BookWsEvent } from "@/lib/book-ws-operation";

const fixture = vi.hoisted(() => ({
  requestedRef: "same",
  get: vi.fn(),
  confirm: vi.fn(),
  reader: vi.fn(),
  spine: vi.fn(),
  fetch: vi.fn(),
  notify: vi.fn(),
  t: (key: string) => key,
}));
vi.mock("next/navigation", () => ({
  useParams: () => ({ bookId: fixture.requestedRef }),
  useSearchParams: () => new URLSearchParams(window.location.search),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: fixture.t }) }));
vi.mock("@/hooks/useAuthStatus", () => ({ useAuthStatus: () => ({ isAdmin: false }) }));
vi.mock("@/lib/notifications", () => ({ notify: fixture.notify }));
vi.mock("@/lib/learning-library", () => ({
  learningLibrary: () => Promise.resolve({ items: [], can_create: true, unavailable_workspaces: [] }),
}));
vi.mock("@/components/learning/LibraryWorkspace", () => ({
  useLearningCreation: () => ({ begin: vi.fn(), dialog: null }),
  requestedLearningCreation: () => false,
}));
vi.mock("@/lib/api", async () => {
  const { scopedUrl } = await import("@/lib/workspace-scope");
  return {
    apiFetch: fixture.fetch,
    apiUrl: (path: string) => scopedUrl(path),
    wsUrl: (path: string) => `ws://localhost${scopedUrl(path)}`,
  };
});
vi.mock("@/lib/use-book-stream", () => ({
  useBookStream: vi.fn(), bookEventKind: () => "", bookEventPageId: () => null,
}));
vi.mock("@/app/(workspace)/learning/books/components/BookLibrary", () => ({ default: () => null }));
vi.mock("@/app/(workspace)/learning/books/components/BookCreator", () => ({ default: () => null }));
vi.mock("@/app/(workspace)/learning/books/components/BookSidebar", () => ({ default: () => null }));
vi.mock("@/app/(workspace)/learning/books/components/BookGenerationActivity", () => ({ default: () => null }));
vi.mock("@/app/(workspace)/learning/books/components/BookPausedBanner", () => ({ default: () => null }));
vi.mock("@/app/(workspace)/learning/books/components/BookHealthBanner", () => ({ default: () => null }));
vi.mock("@/app/(workspace)/learning/books/components/BookChatPanel", () => ({ default: () => null }));
vi.mock("@/app/(workspace)/learning/books/components/LearningCapturePanel", () => ({ default: () => null }));
vi.mock("@/app/(workspace)/learning/books/components/SpineEditor", () => ({
  default: (props: unknown) => { fixture.spine(props); return <div data-testid="spine" />; },
}));
vi.mock("@/app/(workspace)/learning/books/components/PageReader", () => ({
  default: (props: PageReaderProps) => {
    fixture.reader(props);
    return <div data-testid="reader">{props.page?.title}</div>;
  },
}));

import BookPage from "@/app/(workspace)/learning/books/BooksRoute";

// Keep the real bookApi and one-shot operation code. Only the network boundary
// is synthetic, so retries really resolve their URL against the current window.
class Socket implements BookSocketLike {
  static instances: Socket[] = [];
  onopen: BookSocketLike["onopen"] = null;
  onmessage: BookSocketLike["onmessage"] = null;
  onerror: BookSocketLike["onerror"] = null;
  onclose: BookSocketLike["onclose"] = null;
  sent: BookWsEvent[] = [];
  closed = false;
  constructor(readonly url: string) {
    Socket.instances.push(this);
    queueMicrotask(() => this.onopen?.(new Event("open")));
  }
  send(data: string) { this.sent.push(JSON.parse(data) as BookWsEvent); }
  close() { this.closed = true; }
  emit(event: BookWsEvent) { this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(event) })); }
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}
function detail(workspace: string, revision = 1, outline = false): BookDetail {
  return {
    book: { id: "same", ref: "", source: "own", title: workspace, status: outline ? "spine_ready" : "ready", can_edit: true, revision, metadata: {} },
    pages: outline ? [] : [{ id: "page", book_id: "same", title: workspace, status: "ready", blocks: [{ id: "block", type: "text" }] }],
    spine: { book_id: "same", version: 1, chapters: [] },
    progress: { current_page_id: "page", visited_page_ids: [], bookmarked_page_ids: [], quiz_attempts: [] },
  } as unknown as BookDetail;
}
const conflict = { type: "error", status: 409, code: "book_revision_conflict", current_revision: 7 };
const block = { id: "block", type: "text" } as Block;
const settle = () => new Promise<void>(resolve => setTimeout(resolve, 0));

beforeEach(() => {
  fixture.requestedRef = "same";
  window.history.replaceState(null, "", "/learning/books/same?dt_workspace=A");
  vi.stubGlobal("WebSocket", Socket);
  Socket.instances = [];
  fixture.reader.mockReset();
  fixture.spine.mockReset();
  fixture.notify.mockReset();
  fixture.get.mockReset().mockImplementation((workspace: string) => Promise.resolve(detail(workspace, 7)));
  fixture.confirm.mockReset().mockResolvedValue({ pages: detail("A").pages, book_revision: 7 });
  fixture.fetch.mockReset().mockImplementation(async (path: string, init?: RequestInit) => {
    const url = new URL(path, window.location.origin);
    const workspace = url.searchParams.get("dt_workspace") ?? "";
    if (url.pathname === "/api/books/confirm-spine") return Response.json(await fixture.confirm(workspace, JSON.parse(String(init?.body))));
    if (/^\/api\/books\/[^/]+$/.test(url.pathname)) return Response.json(await fixture.get(workspace));
    if (url.pathname.endsWith("/learning-captures")) return Response.json({ captures: [] });
    if (url.pathname.includes("/pages/")) return Response.json({ page: detail(workspace).pages[0] });
    return Response.json({ progress: detail(workspace).progress });
  });
});
afterEach(async () => {
  cleanup();
  await act(async () => {
    for (const socket of Socket.instances) {
      if (!socket.closed) socket.emit({ type: "error", status: 500, message: "test teardown" });
    }
    await settle();
  });
  vi.unstubAllGlobals();
});

async function mounted() {
  const view = render(<BookPage />);
  await screen.findByText("A");
  return view;
}
async function switchToB(view: ReturnType<typeof render>) {
  window.history.replaceState(null, "", "/learning/books/same?dt_workspace=B");
  view.rerender(<BookPage />);
  await screen.findByText("B");
  await act(settle);
}
async function regenerate() {
  await act(async () => {
    void (fixture.reader.mock.lastCall![0] as PageReaderProps).onRegenerateBlock!(block);
    await settle();
  });
}
async function compile() {
  await act(async () => {
    (fixture.reader.mock.lastCall![0] as PageReaderProps).onRecompile!();
    await settle();
  });
}
async function confirm() {
  await act(async () => {
    void fixture.spine.mock.lastCall![0].onConfirm(detail("A").spine as Spine, true, null);
    await settle();
  });
}

describe("detached book mutations", () => {
  it.each(["regenerate", "compile"])("stops an old %s conflict before any request in B", async operation => {
    fixture.get.mockResolvedValueOnce(detail("A", 1));
    const view = await mounted();
    await (operation === "regenerate" ? regenerate() : compile());
    const first = Socket.instances[0];
    expect(first.url).toContain("dt_workspace=A");
    expect(first.sent[0]).toMatchObject({ book_id: "same", page_id: "page", expected_revision: 1 });
    await switchToB(view);
    const requests = fixture.fetch.mock.calls.length;
    await act(async () => { first.emit(conflict); await settle(); });
    expect(Socket.instances.map(socket => socket.url)).not.toContain("ws://localhost/ws/books?dt_workspace=B");
    expect(Socket.instances).toHaveLength(1);
    expect(fixture.fetch).toHaveBeenCalledTimes(requests);
    expect(screen.getByTestId("reader")).toHaveTextContent("B");
    expect(fixture.notify).not.toHaveBeenCalled();
  });

  it("stops compile retry when switching during its conflict refresh", async () => {
    const refresh = deferred<BookDetail>();
    fixture.get.mockResolvedValueOnce(detail("A")).mockReturnValueOnce(refresh.promise);
    const view = await mounted();
    await compile();
    await act(async () => { Socket.instances[0].emit(conflict); await settle(); });
    expect(fixture.get).toHaveBeenCalledTimes(2);
    await switchToB(view);
    const requests = fixture.fetch.mock.calls.length;
    await act(async () => { refresh.resolve(detail("A", 7)); await settle(); });
    expect(Socket.instances.map(socket => socket.url)).not.toContain("ws://localhost/ws/books?dt_workspace=B");
    expect(Socket.instances).toHaveLength(1);
    expect(fixture.fetch).toHaveBeenCalledTimes(requests);
  });

  it.each(["confirmation", "refresh"])("stops post-confirm-spine compilation after switching during %s", async phase => {
    const confirmation = deferred<{ pages: BookDetail["pages"]; book_revision: number }>();
    const refresh = deferred<BookDetail>();
    fixture.get.mockResolvedValueOnce(detail("A", 1, true));
    if (phase === "confirmation") fixture.confirm.mockReturnValueOnce(confirmation.promise);
    else fixture.get.mockReturnValueOnce(refresh.promise);
    const view = render(<BookPage />);
    await screen.findByTestId("spine");
    await confirm();
    if (phase === "refresh") expect(fixture.get).toHaveBeenCalledTimes(2);
    await switchToB(view);
    const requests = fixture.fetch.mock.calls.length;
    await act(async () => {
      if (phase === "confirmation") confirmation.resolve({ pages: detail("A").pages, book_revision: 7 });
      else refresh.resolve(detail("A", 7));
      await settle();
    });
    expect(Socket.instances.map(socket => socket.url)).not.toContain("ws://localhost/ws/books?dt_workspace=B");
    expect(Socket.instances).toHaveLength(0);
    expect(fixture.fetch).toHaveBeenCalledTimes(requests);
    expect(screen.getByTestId("reader")).toHaveTextContent("B");
  });

  it.each(["same", "book:same"])("keeps current own-book regeneration retry bound to %s in A", async ref => {
    fixture.requestedRef = ref;
    fixture.get.mockResolvedValueOnce(detail("A", 1));
    await mounted();
    await regenerate();
    expect(Socket.instances[0].sent[0].expected_revision).toBe(1);
    await act(async () => { Socket.instances[0].emit(conflict); await settle(); });
    expect(Socket.instances).toHaveLength(2);
    const retry = Socket.instances[1];
    expect(retry.url).toContain("dt_workspace=A");
    expect(retry.sent[0]).toMatchObject({ type: "regenerate_block", book_id: ref, page_id: "page", block_id: "block", expected_revision: 7 });
    await act(async () => { retry.emit({ type: "regenerate_block_result", book_revision: 8 }); await settle(); });
    expect(fixture.notify).not.toHaveBeenCalled();
  });

  it("keeps a current canonical compile conflict refresh and retry", async () => {
    fixture.requestedRef = "book:same";
    fixture.get.mockResolvedValueOnce(detail("A", 1));
    await mounted();
    await compile();
    await act(async () => { Socket.instances[0].emit(conflict); await settle(); });
    const retry = Socket.instances[1];
    expect(retry.url).toContain("dt_workspace=A");
    expect(retry.sent[0]).toMatchObject({ type: "compile_page", book_id: "book:same", page_id: "page", expected_revision: 7 });
    await act(async () => { retry.emit({ type: "compile_page_result", page: detail("A", 8).pages[0], book_revision: 8 }); await settle(); });
    expect(fixture.notify).not.toHaveBeenCalled();
  });

  it("keeps current confirm-spine compilation on the confirmed revision", async () => {
    fixture.requestedRef = "book:same";
    fixture.get.mockResolvedValueOnce(detail("A", 1, true));
    render(<BookPage />);
    await screen.findByTestId("spine");
    await confirm();
    await waitFor(() => expect(Socket.instances).toHaveLength(1));
    const socket = Socket.instances[0];
    expect(socket.url).toContain("dt_workspace=A");
    expect(socket.sent[0]).toMatchObject({ type: "compile_page", book_id: "book:same", expected_revision: 7 });
    await act(async () => { socket.emit({ type: "compile_page_result", page: detail("A", 8).pages[0], book_revision: 8 }); await settle(); });
  });
});
