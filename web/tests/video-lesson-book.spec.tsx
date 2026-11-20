// Batch 5 (spec 2026-11-20 §6): video lessons in books, and the composer note
// shown where the entry would be when an admin turned video lessons off.
import { useRef } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MessageSquare } from "lucide-react";

import AnimationBlock from "@/app/(workspace)/learning/books/components/blocks/AnimationBlock";
import BlockRenderer from "@/app/(workspace)/learning/books/components/blocks/BlockRenderer";
import ChatComposer from "@/components/chat/home/ChatComposer";
import AddVideoLessonToBook from "@/components/video-lesson/AddVideoLessonToBook";
import { parseCapabilityCatalogPayload } from "@/features/capabilities/model";
import {
  unavailableCapabilityNotices,
  type CapabilityDef,
} from "@/features/capabilities/presentation";
import { initI18n } from "@/i18n/init";
import type { Block, Book } from "@/lib/book-types";
import type { VideoLessonResult } from "@/lib/video-lesson-types";

initI18n("en");

const api = vi.hoisted(() => ({
  list: vi.fn(),
  get: vi.fn(),
  insertVideoLesson: vi.fn(),
}));
vi.mock("@/lib/book-api", () => ({ bookApi: api }));
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>{children}</a>
  ),
}));
vi.mock("@/lib/workspace-drafts", () => ({
  currentWorkspaceDraftLocation: () => ({ workspaceId: "", pathname: "/chat" }),
  readWorkspaceDraft: vi.fn().mockResolvedValue(null),
  saveWorkspaceDraft: vi.fn().mockResolvedValue(undefined),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  window.history.replaceState(null, "", "/");
});

function block(partial: Partial<Block>): Block {
  return {
    id: "blk_1", type: "video_lesson", status: "ready", title: "", params: {}, payload: {},
    source_anchors: [], metadata: {}, error: "", created_at: 0, updated_at: 0,
    ...partial,
  } as Block;
}

const LESSON_PATH = "/api/books/bk_1/pages/pg_1/blocks/blk_1/media/lesson.html";

describe("video lesson block in a book", () => {
  it("plays the lesson published with the book in a script-only sandbox", () => {
    render(
      <BlockRenderer
        block={block({
          title: "Standing waves",
          payload: { lesson_url: LESSON_PATH, summary: "Nodes.", media: ["lesson.html"], duration_seconds: 80 },
        })}
        onRegenerate={() => {}}
        onChangeType={() => {}}
      />,
    );
    const frame = screen.getByTitle("Standing waves");
    expect(frame).toHaveAttribute("sandbox", "allow-scripts");
    expect(frame.getAttribute("src")).toContain(LESSON_PATH);
    expect(frame.getAttribute("src")).not.toContain("/files/outputs");
    expect(screen.getByText("80 s")).toBeInTheDocument();
    // Inserted from chat: nothing to regenerate or re-type in the book.
    expect(screen.queryByLabelText("Regenerate")).toBeNull();
    expect(screen.queryByLabelText("Change type")).toBeNull();
  });

  it("carries an author path's id so its assigned learners are authorized", () => {
    window.history.replaceState(null, "", "/learning/books/bk_1?mastery_template=tpl_9");
    render(<BlockRenderer block={block({ title: "Waves", payload: { lesson_url: LESSON_PATH } })} />);
    expect(screen.getByTitle("Waves").getAttribute("src")).toContain("mastery_template=tpl_9");
  });

  it("reads an older animation clip through the book instead of the author's outputs", () => {
    const legacy = "/files/outputs/workspace/chat/math_animator/book-bk_1-anim_1/artifacts/clip.mp4";
    const { container } = render(
      <AnimationBlock
        block={block({ id: "anim_1", type: "animation", payload: { video_url: legacy } })}
        bookId="bk_1"
        pageId="pg_1"
      />,
    );
    const src = container.querySelector("video")?.getAttribute("src") ?? "";
    expect(src).toContain("/api/books/bk_1/pages/pg_1/blocks/anim_1/media/clip.mp4");
    cleanup();
    const other = "/files/outputs/workspace/chat/math_animator/turn_9/artifacts/clip.mp4";
    const second = render(
      <AnimationBlock
        block={block({ id: "anim_1", type: "animation", payload: { video_url: other } })}
        bookId="bk_1"
        pageId="pg_1"
      />,
    );
    expect(second.container.querySelector("video")?.getAttribute("src")).toContain(other);
  });
});

const lesson: VideoLessonResult = {
  response: "Nodes and antinodes.",
  title: "Standing waves",
  page: { type: "html", url: "/files/outputs/workspace/chat/video_lesson/t1/artifacts/lesson.html", filename: "lesson.html" },
  code: "",
  durationSeconds: 80,
  attempts: 1,
};

describe("adding a chat video lesson to a book", () => {
  it("inserts it into the chosen chapter of an editable book", async () => {
    api.list.mockResolvedValue({
      can_create: true,
      books: [
        { id: "bk_mine", title: "Waves", revision: 4, page_count: 2, can_edit: true } as Book,
        { id: "bk_theirs", title: "Read only", revision: 1, page_count: 3, can_edit: false } as Book,
      ],
    });
    api.get.mockResolvedValue({ pages: [{ id: "pg_1", title: "Nodes" }, { id: "pg_2", title: "Beats" }] });
    api.insertVideoLesson.mockResolvedValue({ block: {}, book_revision: 5 });

    render(<AddVideoLessonToBook result={lesson} />);
    fireEvent.click(screen.getByRole("button", { name: /Add to book/ }));
    const bookSelect = await screen.findByRole("combobox", { name: "Book" });
    expect(screen.queryByRole("option", { name: "Read only" })).toBeNull();
    fireEvent.change(bookSelect, { target: { value: "bk_mine" } });
    const chapter = await screen.findByRole("combobox", { name: "Chapter" });
    fireEvent.change(chapter, { target: { value: "pg_2" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    await waitFor(() => expect(api.insertVideoLesson).toHaveBeenCalledTimes(1));
    expect(api.insertVideoLesson).toHaveBeenCalledWith({
      book_id: "bk_mine",
      page_id: "pg_2",
      output_url: lesson.page.url,
      title: "Standing waves",
      summary: "Nodes and antinodes.",
      duration_seconds: 80,
      expected_revision: 4,
    });
    expect(await screen.findByText("Added to “Waves”")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open the chapter" })).toHaveAttribute(
      "href", expect.stringContaining("/learning/books/bk_mine/pages/pg_2"),
    );
  });

  it("shows the server's reason when the insert is refused", async () => {
    api.list.mockResolvedValue({
      can_create: true,
      books: [{ id: "bk_mine", title: "Waves", revision: 1, page_count: 1, can_edit: true } as Book],
    });
    api.get.mockResolvedValue({ pages: [{ id: "pg_1", title: "Nodes" }] });
    api.insertVideoLesson.mockRejectedValue(
      new Error("book api /books/insert-video-lesson → 404: Video lesson not found"),
    );
    render(<AddVideoLessonToBook result={lesson} />);
    fireEvent.click(screen.getByRole("button", { name: /Add to book/ }));
    fireEvent.change(await screen.findByRole("combobox", { name: "Book" }), { target: { value: "bk_mine" } });
    await screen.findByRole("combobox", { name: "Chapter" });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Video lesson not found");
  });
});

const chat: CapabilityDef = {
  value: "", label: "Chat", description: "Flexible conversation with any tool",
  icon: MessageSquare, allowedTools: [],
};

function Composer({ capabilities, onSelect }: { capabilities: CapabilityDef[]; onSelect: (v: string) => void }) {
  const refs = {
    composerRef: useRef<HTMLDivElement>(null),
    capMenuRef: useRef<HTMLDivElement>(null),
    capBtnRef: useRef<HTMLButtonElement>(null),
    spaceMenuRef: useRef<HTMLDivElement>(null),
    spaceBtnRef: useRef<HTMLButtonElement>(null),
    dragCounter: useRef(0),
  };
  const noop = () => {};
  return (
    <ChatComposer
      {...refs}
      dragging={false} capMenuOpen spaceMenuOpen={false} hasMessages={false}
      attachments={[]} attachmentError={null} activeCap={chat} knowledgeBases={[]}
      llmOptions={[]} activeLLMDefault={null} llmSelection={null} llmOptionsLoading={false}
      llmOptionsError={false} selectedNotebookRecords={[]} selectedBookReferences={[]}
      selectedHistorySessions={[]} selectedAgentSessions={[]} selectedQuestionEntries={[]}
      notebookReferenceGroups={[]} selectedPersona={null} selectedMemoryFiles={[]}
      selectedKnowledgeBases={[]} isStreaming={false} isVisualizeMode={false}
      capabilityNeedsConfig={false} capabilityConfigConfirmed={false}
      onRequestConfigConfirm={noop} capabilities={capabilities} onSetCapMenuOpen={noop}
      onSetSpaceMenuOpen={noop} onToggleKB={noop} onSelectLLM={noop}
      onSelectNotebookPicker={noop} onSelectBookPicker={noop} onSelectHistoryPicker={noop}
      onSelectAgentsPicker={noop} onSelectQuestionBankPicker={noop} onSelectPersonaPicker={noop}
      onSelectMemoryPicker={noop} onClearPersona={noop} onToggleMemoryFile={noop} onSend={noop}
      onRemoveAttachment={noop} onRemoveHistory={noop} onRemoveAgent={noop}
      onRemoveBookReference={noop} onRemoveNotebook={noop} onRemoveQuestion={noop}
      onDragEnter={noop} onDragLeave={noop} onDragOver={noop} onDrop={noop} onPaste={noop}
      onAddFiles={noop} onSelectCapability={onSelect} onCancelStreaming={noop}
    />
  );
}

describe("video lessons turned off by the administrator", () => {
  it("shows a note where the entry would be instead of hiding it", () => {
    const notices = unavailableCapabilityNotices(parseCapabilityCatalogPayload({
      capabilities: [
        { id: "chat", available: true },
        { id: "video_lesson", available: false, unavailable_reason: "disabled_by_admin" },
        { id: "offline_plugin", available: false, unavailable_reason: "disabled_by_admin" },
      ],
    }));
    expect(notices.map((item) => item.value)).toEqual(["video_lesson"]);
    const onSelect = vi.fn();
    render(<Composer capabilities={[chat, ...notices]} onSelect={onSelect} />);
    const note = screen.getByTestId("capability-unavailable-video_lesson");
    expect(note).toHaveTextContent("Video Lesson");
    expect(note).toHaveTextContent("Video lessons are turned off by the administrator");
    expect(note).toHaveAttribute("role", "note");
    expect(note.closest("button")).toBeNull();
    fireEvent.click(note);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("explains a missing server runtime too, and says nothing when it is available", () => {
    const [runtime] = unavailableCapabilityNotices(parseCapabilityCatalogPayload({
      capabilities: [{ id: "video_lesson", available: false, unavailable_reason: "runtime_unavailable" }],
    }));
    expect(runtime.unavailableNote).toMatch(/contact your administrator/);
    expect(unavailableCapabilityNotices(parseCapabilityCatalogPayload({
      capabilities: [{ id: "video_lesson", available: true, unavailable_reason: "" }],
    }))).toEqual([]);
  });
});
