import { act, render, screen } from "@testing-library/react";
import { createRef } from "react";
import { beforeEach, expect, it, vi } from "vitest";
import { initI18n } from "@/i18n/init";
import type { SessionViewerPanelHandle } from "@/components/chat/home/SessionViewerPanel";
import type { MessageAttachment } from "@/features/chat/ChatStateAdapter";
import { resolveSourceUrl } from "@/components/chat/preview/previewerFor";

// ── mocks ────────────────────────────────────────────────────────────

vi.mock("next/dynamic", () => ({
  __esModule: true,
  default: (_loader: () => Promise<{ default: React.FC<any> }>) => {
    const Stub: React.FC<any> = (props) => (
      <div data-testid="dynamic-stub" data-props={JSON.stringify(props)} />
    );
    Stub.displayName = "DynamicStub";
    return Stub;
  },
}));

vi.mock("react-i18next", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-i18next")>();
  return {
    ...actual,
    useTranslation: () => ({ t: (k: string) => k }),
  };
});

vi.mock("@/lib/api", () => ({
  apiUrl: (path: string) => `http://test${path}`,
}));

vi.mock("@/components/chat/home/SessionActivityPanel", () => ({
  ActivityBody: () => <div data-testid="activity-body" />,
}));

vi.mock("@/components/quiz/QuizFollowupTabBody", () => ({
  __esModule: true,
  default: () => null,
}));

vi.mock("@/components/chat/home/ConsultationTabBody", () => ({
  __esModule: true,
  default: () => null,
}));

vi.mock("@/shared/storage", () => ({
  browserStorage: {
    readRaw: () => null,
    writeRaw: () => {},
  },
}));

// Partial mock: spy on resolveSourceUrl so we can record which source.url
// the previewer receives, while keeping the real implementation.
vi.mock("@/components/chat/preview/previewerFor", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("@/components/chat/preview/previewerFor")
  >();
  return {
    ...actual,
    resolveSourceUrl: vi.fn(actual.resolveSourceUrl),
  };
});

initI18n("en");

// ── helpers ──────────────────────────────────────────────────────────

function emptyActivity() {
  return {
    tools: [],
    knowledgeBases: [],
    space: { name: "", spaces: [] },
    attachments: [],
    artifacts: [],
    isEmpty: true,
  };
}

// ── tests ────────────────────────────────────────────────────────────

let SessionViewerPanel: typeof import("@/components/chat/home/SessionViewerPanel").default;

beforeEach(async () => {
  const mod = await import("@/components/chat/home/SessionViewerPanel");
  SessionViewerPanel = mod.default;
  vi.mocked(resolveSourceUrl).mockClear();
});

it("clicking two same-name images delivers each source.url to the previewer", () => {
  const ref = createRef<SessionViewerPanelHandle>();

  render(
    <SessionViewerPanel
      ref={ref}
      open
      sessionId="s1"
      onClose={vi.fn()}
      onAutoOpen={vi.fn()}
      activity={emptyActivity() as any}
    />,
  );

  const imgA: MessageAttachment = {
    type: "image",
    filename: "chart.png",
    url: "/files/attachments/aaa",
    mime_type: "image/png",
    workspace_item_id: "ws-1",
    origin: "workspace",
  };
  const imgB: MessageAttachment = {
    type: "image",
    filename: "chart.png",
    url: "/files/attachments/bbb",
    mime_type: "image/png",
    workspace_item_id: "ws-2",
    origin: "workspace",
  };

  // Open first image
  act(() => ref.current!.openFileTab(imgA));

  const callsAfterA = vi.mocked(resolveSourceUrl).mock.calls;
  const lastSourceA = callsAfterA[callsAfterA.length - 1][0];
  expect(lastSourceA.url).toBe("/files/attachments/aaa");

  // The download link corroborates
  expect((screen.getByTitle("Download") as HTMLAnchorElement).href).toContain(
    "/files/attachments/aaa",
  );

  // Open second image (same filename, different attachment)
  act(() => ref.current!.openFileTab(imgB));

  const callsAfterB = vi.mocked(resolveSourceUrl).mock.calls;
  const lastSourceB = callsAfterB[callsAfterB.length - 1][0];
  expect(lastSourceB.url).toBe("/files/attachments/bbb");

  expect((screen.getByTitle("Download") as HTMLAnchorElement).href).toContain(
    "/files/attachments/bbb",
  );
});

it("clicking the same image twice does not duplicate tabs", () => {
  const ref = createRef<SessionViewerPanelHandle>();

  render(
    <SessionViewerPanel
      ref={ref}
      open
      sessionId="s2"
      onClose={vi.fn()}
      onAutoOpen={vi.fn()}
      activity={emptyActivity() as any}
    />,
  );

  const img: MessageAttachment = {
    type: "image",
    filename: "photo.png",
    url: "/files/attachments/xxx",
    mime_type: "image/png",
    id: "att-1",
  };

  act(() => ref.current!.openFileTab(img));
  act(() => ref.current!.openFileTab(img));

  const allButtons = screen.getAllByRole("button");
  const fileTabs = allButtons.filter((b) =>
    b.textContent?.includes("photo.png"),
  );
  expect(fileTabs).toHaveLength(1);
});
