import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import BookShareDialog from "@/app/(workspace)/learning/books/components/BookShareDialog";
import type { Book } from "@/lib/book-types";

const fixture = vi.hoisted(() => ({ sharing: vi.fn(), setVisibility: vi.fn(), t: (key: string) => key }));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: fixture.t }) }));
vi.mock("@/lib/book-api", () => ({
  BookApiError: class extends Error {},
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
