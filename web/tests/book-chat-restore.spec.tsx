import { StrictMode } from "react";
import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import BookChatPanel from "@/app/(workspace)/learning/books/components/BookChatPanel";
import type { Book, Page } from "@/lib/book-types";

const fixture = vi.hoisted(() => ({
  session: vi.fn(),
  t: (key: string) => key,
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: fixture.t }) }));
vi.mock("@/context/AppShellContext", () => ({ useAppShell: () => ({ language: "en" }) }));
vi.mock("@/lib/session-api", () => ({ getSession: fixture.session }));
vi.mock("@/lib/attachment-limits", () => ({ useAttachmentLimits: () => ({ maxFileBytes: 1000, maxTotalBytes: 1000 }) }));
vi.mock("@/components/common/AssistantResponse", () => ({
  default: ({ content }: { content: string }) => <p>{content}</p>,
}));

it("restores a bound page session after StrictMode cancels the first load", async () => {
  fixture.session.mockImplementation(() => Promise.resolve({
    messages: [{ role: "assistant", content: "Canonical restored answer" }],
  }));
  render(<StrictMode>
    <BookChatPanel book={{ id: "same", language: "en" } as Book}
      requestedRef="book:same" page={{ id: "page" } as Page}
      open initialSessionId="canonical-session" onClose={vi.fn()} />
  </StrictMode>);
  expect(await screen.findByText("Canonical restored answer")).toBeVisible();
  expect(fixture.session).toHaveBeenCalledWith("canonical-session");
});
