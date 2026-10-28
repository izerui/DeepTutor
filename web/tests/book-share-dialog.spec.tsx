import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import BookShareDialog from "@/app/(workspace)/learning/books/components/BookShareDialog";
import { BookApiError } from "@/lib/book-api";
import type { Book } from "@/lib/book-types";

const fixture = vi.hoisted(() => ({ sharing: vi.fn(), setVisibility: vi.fn(), t: (key: string) => key }));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: fixture.t }) }));
vi.mock("@/lib/book-api", async () => ({
  ...(await vi.importActual<typeof import("@/lib/book-api")>("@/lib/book-api")),
  bookApi: { sharing: fixture.sharing, setVisibility: fixture.setVisibility },
}));
const book = { id: "same", ref: "book:same", visibility: "private", owner_id: "owner", content_workspace_id: "w" } as Book;
const sharing = {
  visibility: "private", can_set_visibility: true,
  kbs: { private_kbs: [{ kb_id: "kb1", name: "Physics", default: "make_public" }], issues: [], missing: [], public_kbs: [] },
  references: [],
};
beforeEach(() => {
  fixture.sharing.mockReset().mockResolvedValue(sharing);
  fixture.setVisibility.mockReset().mockResolvedValue({});
});
it("defaults private KB choices to the server plan and submits reference-only using the bound scope", async () => {
  const close = vi.fn();
  render(<BookShareDialog book={book} isAdmin={false} onClose={close} onChanged={vi.fn()} />);
  const select = await screen.findByRole("combobox", { name: "Physics" });
  expect(select).toHaveValue("make_public");
  fireEvent.change(select, { target: { value: "reference_only" } });
  fireEvent.click(screen.getByRole("button", { name: "Make public" }));
  await waitFor(() => expect(close).toHaveBeenCalled());
  expect(fixture.setVisibility).toHaveBeenCalledWith("book:same", {
    visibility: "public", kb_choices: { kb1: "reference_only" }, force: false,
  }, "w");
});
it("keeps dismissal available after loading failure and never enables submission", async () => {
  fixture.sharing.mockRejectedValue(new Error("Unavailable"));
  const close = vi.fn();
  render(<BookShareDialog book={book} isAdmin={false} onClose={close} onChanged={vi.fn()} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Unavailable");
  expect(screen.getByRole("button", { name: "Make public" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(close).toHaveBeenCalled();
});
it("discards old plans when the selected workspace changes", async () => {
  let resolve!: (value: unknown) => void;
  fixture.sharing.mockReturnValueOnce(new Promise(done => { resolve = done; }));
  const props = { isAdmin: false, onClose: vi.fn(), onChanged: vi.fn() };
  const { rerender } = render(<BookShareDialog {...props} book={book} />);
  rerender(<BookShareDialog {...props} book={{ ...book, content_workspace_id: "next" }} />);
  await screen.findByRole("combobox", { name: "Physics" });
  await act(async () => resolve({ ...sharing, kbs: { ...sharing.kbs, private_kbs: [] } }));
  expect(screen.getByRole("combobox", { name: "Physics" })).toBeVisible();
});
it("student owner of a public book can withdraw but not publish (B2-11)", async () => {
  const publicBook = { ...book, visibility: "public" as const } as Book;
  fixture.sharing.mockResolvedValue({
    ...sharing,
    visibility: "public",
    can_set_visibility: true,
    can_make_public: false,
    kbs: { ...sharing.kbs, private_kbs: [] },
  });
  render(<BookShareDialog book={publicBook} isAdmin={false} onClose={vi.fn()} onChanged={vi.fn()} />);
  // Flush the bookApi.sharing mock's resolved promise and the resulting
  // setState so the dialog exits its loading state before we query the DOM.
  await act(async () => {});
  expect(screen.getByRole("button", { name: "Make private" })).toBeEnabled();
});
it("student owner blocked from publishing a private book when can_make_public is false (B2-11)", async () => {
  fixture.sharing.mockResolvedValue({
    ...sharing,
    visibility: "private",
    can_set_visibility: true,
    can_make_public: false,
    kbs: { ...sharing.kbs, private_kbs: [] },
  });
  render(<BookShareDialog book={book} isAdmin={false} onClose={vi.fn()} onChanged={vi.fn()} />);
  const btn = await screen.findByRole("button", { name: "Make public" });
  expect(btn).toBeDisabled();
});
it("blocks publication issues until an administrator explicitly chooses force", async () => {
  fixture.sharing.mockResolvedValue({ ...sharing, kbs: { ...sharing.kbs, issues: [{ kb_id: "c", ref: "kb:c", name: "Connector", code: "connector" }] } });
  const { rerender } = render(<BookShareDialog book={book} isAdmin={false} onClose={vi.fn()} onChanged={vi.fn()} />);
  await screen.findByText(/Personal connectors/);
  expect(screen.getByRole("button", { name: "Make public" })).toBeDisabled();
  expect(screen.queryByRole("checkbox")).toBeNull();
  rerender(<BookShareDialog book={book} isAdmin onClose={vi.fn()} onChanged={vi.fn()} />);
  fireEvent.click(screen.getByRole("checkbox"));
  expect(screen.getByRole("button", { name: "Make public" })).toBeEnabled();
});
it("lets the owner withdraw a referenced book after confirming teacher-path counts (R1)", async () => {
  const publicBook = { ...book, visibility: "public" as const } as Book;
  fixture.sharing.mockResolvedValue({
    ...sharing, visibility: "public", kbs: { ...sharing.kbs, private_kbs: [] },
    references: [], other_references: { total: 2, by_kind: { mastery_template: 2 } },
  });
  fixture.setVisibility.mockRejectedValueOnce(new BookApiError("conflict", 409, "referenced_by_teacher_paths", undefined, {
    code: "referenced_by_teacher_paths", affected_paths: 2, affected_students: 7,
  }));
  const close = vi.fn();
  render(<BookShareDialog book={publicBook} isAdmin={false} onClose={close} onChanged={vi.fn()} />);
  expect(await screen.findByText("{{count}} items from other accounts use this material and will show it as unavailable.")).toBeTruthy();
  const button = screen.getByRole("button", { name: "Make private" });
  expect(button).toBeEnabled();
  fireEvent.click(button);
  expect(await screen.findByRole("alert")).toHaveTextContent("{{count}} published teacher paths use this material");
  expect(fixture.setVisibility).toHaveBeenLastCalledWith("book:same", {
    visibility: "private", kb_choices: {}, force: false,
  }, "w");
  fireEvent.click(screen.getByRole("button", { name: "Make it private anyway" }));
  await waitFor(() => expect(close).toHaveBeenCalled());
  expect(fixture.setVisibility).toHaveBeenLastCalledWith("book:same", {
    visibility: "private", kb_choices: {}, force: false,
    confirm: true, expected_paths: 2, expected_students: 7,
  }, "w");
});
