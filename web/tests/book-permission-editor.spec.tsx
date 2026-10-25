import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { BookPermissionEditor } from "@/features/multi-user/components/BookPermissionEditor";
import { fetchBookPermission, saveBookPermission } from "@/features/multi-user/api";
import type { BookPermission } from "@/features/multi-user/types";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock("@/features/multi-user/api", () => ({
  fetchBookPermission: vi.fn(),
  saveBookPermission: vi.fn(),
  fetchAdminBooks: vi.fn().mockResolvedValue([]),
}));

const fetched = vi.mocked(fetchBookPermission);
const saved = vi.mocked(saveBookPermission);
const checkbox = () => screen.getByRole("checkbox", { name: "May create personal books" });
const save = () => screen.getByRole("button", { name: "Save" });
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

beforeEach(() => {
  fetched.mockReset().mockResolvedValue({ create: true } as BookPermission);
  saved.mockReset().mockImplementation(async (_id, value) => value);
});

it("loads, toggles, saves strictly create-only, and reloads persisted permission", async () => {
  fetched.mockResolvedValue({ create: true, default: "read", books: { old: "edit" } } as BookPermission);
  const view = render(<BookPermissionEditor userId="A" />);
  await waitFor(() => expect(checkbox()).toBeChecked());
  fireEvent.click(checkbox());
  fireEvent.click(save());
  await screen.findByText("Saved");
  expect(saved).toHaveBeenCalledWith("A", { create: false });
  expect(save()).toBeDisabled();
  view.unmount();
  fetched.mockResolvedValue({ create: false } as BookPermission);
  render(<BookPermissionEditor userId="A" />);
  await waitFor(() => expect(checkbox()).not.toBeChecked());
});

it.each(["resolve", "reject"] as const)("ignores a delayed old-user fetch %s", async (completion) => {
  const pending = deferred<BookPermission>();
  fetched.mockReturnValueOnce(pending.promise);
  const view = render(<BookPermissionEditor userId="A" />);
  view.rerender(<BookPermissionEditor userId="B" />);
  await waitFor(() => expect(checkbox()).toBeChecked());
  await act(async () => {
    if (completion === "resolve") pending.resolve({ create: false } as BookPermission);
    else pending.reject(new Error("old fetch error"));
  });
  expect(checkbox()).toBeChecked();
  expect(screen.queryByText("old fetch error")).not.toBeInTheDocument();
  fireEvent.click(checkbox());
  fireEvent.click(save());
  await waitFor(() => expect(saved).toHaveBeenCalledWith("B", { create: false }));
});

it.each(["resolve", "reject"] as const)("resets pending save and ignores old-user save %s", async (completion) => {
  const pending = deferred<BookPermission>();
  saved.mockReturnValueOnce(pending.promise);
  const view = render(<BookPermissionEditor userId="A" />);
  await waitFor(() => expect(checkbox()).toBeChecked());
  fireEvent.click(checkbox());
  fireEvent.click(save());
  expect(checkbox()).toBeDisabled();
  const loadB = deferred<BookPermission>();
  fetched.mockReturnValueOnce(loadB.promise);
  view.rerender(<BookPermissionEditor userId="B" />);
  expect(screen.getByText("Loading book permissions…")).toBeInTheDocument();
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  await act(async () => loadB.resolve({ create: true } as BookPermission));
  expect(checkbox()).toBeEnabled();
  await act(async () => {
    if (completion === "resolve") pending.resolve({ create: false } as BookPermission);
    else pending.reject(new Error("old save error"));
  });
  expect(checkbox()).toBeChecked();
  expect(screen.queryByText("Saved")).not.toBeInTheDocument();
  expect(screen.queryByText("old save error")).not.toBeInTheDocument();
});

it("prevents conflicting edits during save, then keeps the next choice dirty", async () => {
  const pending = deferred<BookPermission>();
  saved.mockReturnValueOnce(pending.promise);
  render(<BookPermissionEditor userId="A" />);
  await waitFor(() => expect(checkbox()).toBeChecked());
  fireEvent.click(checkbox());
  fireEvent.click(save());
  expect(checkbox()).toBeDisabled();
  expect(save()).toBeDisabled();
  await act(async () => pending.resolve({ create: false } as BookPermission));
  fireEvent.click(checkbox());
  expect(checkbox()).toBeChecked();
  expect(save()).toBeEnabled();
  expect(screen.queryByText("Saved")).not.toBeInTheDocument();
  fireEvent.click(save());
  await waitFor(() => expect(saved).toHaveBeenLastCalledWith("A", { create: true }));
});

it("keeps a failed load disabled and clears its error on a user switch", async () => {
  fetched.mockRejectedValueOnce(new Error("load failed"));
  const view = render(<BookPermissionEditor userId="A" />);
  await screen.findByText("load failed");
  expect(checkbox()).toBeDisabled();
  expect(save()).toBeDisabled();
  view.rerender(<BookPermissionEditor userId="B" />);
  await waitFor(() => expect(checkbox()).toBeEnabled());
  expect(screen.queryByText("load failed")).not.toBeInTheDocument();
});

it("preserves unsaved choice after a save error and supports retry", async () => {
  saved.mockRejectedValueOnce(new Error("save failed"));
  render(<BookPermissionEditor userId="A" />);
  await waitFor(() => expect(checkbox()).toBeChecked());
  fireEvent.click(checkbox());
  fireEvent.click(save());
  await screen.findByText("save failed");
  expect(checkbox()).not.toBeChecked();
  expect(save()).toBeEnabled();
  fireEvent.click(save());
  await screen.findByText("Saved");
  expect(screen.queryByText("save failed")).not.toBeInTheDocument();
});

it("ignores save failure after unmount and does not affect a remounted same user", async () => {
  const pending = deferred<BookPermission>();
  saved.mockReturnValueOnce(pending.promise);
  const view = render(<BookPermissionEditor userId="A" />);
  await waitFor(() => expect(checkbox()).toBeChecked());
  fireEvent.click(checkbox());
  fireEvent.click(save());
  view.unmount();
  render(<BookPermissionEditor userId="A" />);
  await waitFor(() => expect(checkbox()).toBeChecked());
  await act(async () => pending.reject(new Error("unmounted error")));
  expect(screen.queryByText("unmounted error")).not.toBeInTheDocument();
  expect(checkbox()).toBeEnabled();
});
