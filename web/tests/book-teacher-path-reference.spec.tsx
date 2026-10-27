import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { BookDetail } from "@/lib/book-types";
import type { PageReaderProps } from "@/app/(workspace)/learning/books/components/PageReader";

const fixture = vi.hoisted(() => ({
  reader: vi.fn(),
  health: vi.fn(),
  fetch: vi.fn(),
  notify: vi.fn(),
  book: {} as Record<string, unknown>,
  t: (key: string) => key,
}));
vi.mock("next/navigation", () => ({
  useParams: () => ({ bookId: "bk_ref" }),
  useSearchParams: () => new URLSearchParams(window.location.search),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: fixture.t }) }));
vi.mock("@/hooks/useAuthStatus", () => ({ useAuthStatus: () => ({ isAdmin: false }) }));
vi.mock("@/lib/notifications", () => ({ notify: fixture.notify }));
vi.mock("@/lib/learning-library", () => ({
  learningLibrary: () => Promise.resolve({ items: [], can_create: false, unavailable_workspaces: [] }),
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
vi.mock("@/app/(workspace)/learning/books/components/BookHealthBanner", () => ({
  default: () => { fixture.health(); return null; },
}));
vi.mock("@/app/(workspace)/learning/books/components/BookChatPanel", () => ({ default: () => null }));
vi.mock("@/app/(workspace)/learning/books/components/LearningCapturePanel", () => ({ default: () => null }));
vi.mock("@/app/(workspace)/learning/books/components/SpineEditor", () => ({ default: () => null }));
vi.mock("@/app/(workspace)/learning/books/components/PageReader", () => ({
  default: (props: PageReaderProps) => {
    fixture.reader(props);
    return <div data-testid="reader">{props.page?.title}</div>;
  },
}));

import BookPage from "@/app/(workspace)/learning/books/BooksRoute";

const PRIVATE_COPY =
  "Shared book (read only): your reading progress, bookmarks and captures remain private to you.";

function detail(): BookDetail {
  return {
    book: {
      id: "bk_ref", ref: "book:bk_ref", title: "Waves", status: "ready", revision: 1, metadata: {},
      source: "shared", permission: "read", can_edit: false, group: "public", ...fixture.book,
    },
    pages: [{ id: "page", book_id: "bk_ref", title: "Chapter one", status: "ready", blocks: [{ id: "b", type: "text" }] }],
    spine: { book_id: "bk_ref", version: 1, chapters: [] },
    progress: { current_page_id: "page", visited_page_ids: [], bookmarked_page_ids: [], quiz_attempts: [] },
  } as unknown as BookDetail;
}
const settle = () => new Promise<void>(resolve => setTimeout(resolve, 0));
const paths = () => fixture.fetch.mock.calls.map(([path]) => new URL(path, window.location.origin).pathname);

beforeEach(() => {
  fixture.reader.mockReset();
  fixture.health.mockReset();
  fixture.notify.mockReset();
  fixture.book = {};
  fixture.fetch.mockReset().mockImplementation(async (path: string) => {
    const url = new URL(path, window.location.origin);
    if (/^\/api\/books\/[^/]+$/.test(url.pathname)) return Response.json(detail());
    if (url.pathname.includes("/pages/")) return Response.json({ page: detail().pages[0] });
    // Everything else on a reference-only book is outside the path's grant.
    return Response.json({ detail: "Book not found" }, { status: 404 });
  });
});
afterEach(() => cleanup());

async function open(search: string) {
  window.history.replaceState(null, "", `/learning/books/bk_ref${search}`);
  render(<BookPage />);
  await screen.findByTestId("reader");
  await act(settle);
  return fixture.reader.mock.lastCall![0] as PageReaderProps;
}

it("a private book read through a teacher path keeps the reader's own learning controls", async () => {
  fixture.book = { visibility: "private" };
  const block = detail().pages[0].blocks[0];
  fixture.fetch.mockImplementation(async (path: string, init?: RequestInit) => {
    const url = new URL(path, window.location.origin);
    if (/^\/api\/books\/[^/]+$/.test(url.pathname)) return Response.json(detail());
    if (url.pathname.includes("/pages/")) return Response.json({ page: detail().pages[0] });
    if (url.pathname.endsWith("/learning-captures")) {
      return init?.method === "POST"
        ? Response.json({
            capture: {
              id: "cap_1", book_id: "bk_ref", page_id: "page", block_id: "b",
              source_text: "Nodes stay still.", status: "captured", updated_at: 1,
            },
          })
        : Response.json({ captures: [] });
    }
    return Response.json({ progress: detail().progress });
  });
  const reader = await open("?mastery_template=tpl_1");
  expect(reader.onToggleBookmark).toBeTypeOf("function");
  expect(reader.onQuizAttempt).toBeTypeOf("function");
  expect(reader.onCaptureSelection).toBeTypeOf("function");
  // Reading state is the reader's own, like any shared book's.
  expect(screen.getByText(PRIVATE_COPY)).toBeVisible();
  // Book health is an editor surface: still left out.
  expect(fixture.health).not.toHaveBeenCalled();

  await act(async () => {
    reader.onToggleBookmark!();
    reader.onQuizAttempt!(block, { questionId: "q1", userAnswer: "a", isCorrect: true });
    reader.onCaptureSelection!({ page_id: "page", block_id: "b", source_text: "Nodes stay still." });
    await settle();
  });

  const writes = fixture.fetch.mock.calls
    .map(([path, init]) => ({ path: new URL(path, window.location.origin).pathname, init: init as RequestInit }))
    .filter(({ init }) => init?.method === "POST");
  const byPath = new Map(writes.map(call => [call.path, call]));
  for (const path of [
    "/api/books/progress/visit",
    "/api/books/progress/bookmark",
    "/api/books/quiz-attempt",
    "/api/books/bk_ref/learning-captures",
  ]) {
    const call = byPath.get(path);
    expect(call, path).toBeDefined();
    // The path travels with every write, so the server can open the grant.
    expect((call!.init.headers as Record<string, string>)["X-DeepTutor-Mastery-Template"]).toBe("tpl_1");
  }
  expect(JSON.parse(String(byPath.get("/api/books/progress/bookmark")!.init.body))).toMatchObject({
    book_id: "bk_ref",
    page_id: "page",
  });
  expect(fixture.notify).not.toHaveBeenCalled();
});

it.each([
  ["a public book opened from a teacher path", "?mastery_template=tpl_1", "public"],
  ["a private shared book opened normally", "", "private"],
])("%s keeps its learning controls", async (_name, search, visibility) => {
  fixture.book = { visibility };
  fixture.fetch.mockImplementation(async (path: string) => {
    const url = new URL(path, window.location.origin);
    if (/^\/api\/books\/[^/]+$/.test(url.pathname)) return Response.json(detail());
    if (url.pathname.includes("/pages/")) return Response.json({ page: detail().pages[0] });
    if (url.pathname.endsWith("/learning-captures")) return Response.json({ captures: [] });
    return Response.json({ progress: detail().progress });
  });
  const reader = await open(search);
  expect(reader.onToggleBookmark).toBeTypeOf("function");
  expect(reader.onQuizAttempt).toBeTypeOf("function");
  expect(reader.onCaptureSelection).toBeTypeOf("function");
  expect(screen.getByText(PRIVATE_COPY)).toBeVisible();
  expect(paths()).toContain("/api/books/progress/visit");
});
