import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const fixture = vi.hoisted(() => ({
  sharing: vi.fn(),
  t: (key: string) => key,
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: fixture.t }) }));
vi.mock("@/lib/book-api", () => ({
  bookApi: { sharing: fixture.sharing, setVisibility: vi.fn() },
  bookWithdrawalImpact: vi.fn(() => null),
}));
vi.mock("@/lib/book-errors", () => ({
  bookErrorMessage: (err: unknown) => String(err),
}));
vi.mock("@/lib/book-sharing", () => ({
  bookRequestRef: (book: Record<string, unknown>) => book.ref || book.id,
  bookSelectionKey: (...args: unknown[]) => JSON.stringify(args),
}));
vi.mock("@/lib/withdrawal-confirmation", () => ({
  confirmationFields: () => ({}),
  describeOtherReferences: () => "",
  describeWithdrawalImpact: () => "",
  NO_OTHER_REFERENCES: { total: 0 },
}));
vi.mock("@/shared/ui/Dialog", () => ({
  Dialog: ({ children, title }: { children: React.ReactNode; title: string }) => (
    <div data-testid="dialog" data-title={title}>{children}</div>
  ),
}));

import BookShareDialog from "@/app/(workspace)/learning/books/components/BookShareDialog";

afterEach(() => {
  cleanup();
  fixture.sharing.mockReset();
});

const publicBook = (group: "mine" | "public") => ({
  id: "bk_test",
  ref: "book:bk_test",
  title: "Test Book",
  visibility: "public" as const,
  group,
  content_workspace_id: "",
  can_set_visibility: true,
});

describe("B3-P14 BookShareDialog admin non-owner wording", () => {
  it("admin non-owner sees neutral text without 'you'", async () => {
    fixture.sharing.mockResolvedValue({
      visibility: "public",
      can_set_visibility: true,
      can_make_public: false,
      kbs: { private_kbs: [], issues: [], missing: [] },
      other_references: { total: 0 },
    });
    render(
      <BookShareDialog
        book={publicBook("public") as never}
        isAdmin={true}
        onClose={vi.fn()}
        onChanged={vi.fn()}
      />,
    );

    // The dialog should show the owner-neutral message.
    const text = await screen.findByText(
      "Only the book's owner and admins can open a private book, plus anything that references it.",
    );
    expect(text).toBeTruthy();
    // The old "Only you and admins" should NOT appear.
    expect(
      screen.queryByText(
        "Only you and admins can open a private book, plus anything that references it.",
      ),
    ).toBeNull();
  });

  it("owner still sees the 'you' wording", async () => {
    fixture.sharing.mockResolvedValue({
      visibility: "public",
      can_set_visibility: true,
      can_make_public: true,
      kbs: { private_kbs: [], issues: [], missing: [] },
      other_references: { total: 0 },
    });
    render(
      <BookShareDialog
        book={publicBook("mine") as never}
        isAdmin={true}
        onClose={vi.fn()}
        onChanged={vi.fn()}
      />,
    );

    const text = await screen.findByText(
      "Only you and admins can open a private book, plus anything that references it.",
    );
    expect(text).toBeTruthy();
    expect(
      screen.queryByText(
        "Only the book's owner and admins can open a private book, plus anything that references it.",
      ),
    ).toBeNull();
  });
});
