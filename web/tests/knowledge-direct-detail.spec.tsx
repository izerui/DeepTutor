import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import KnowledgePage from "@/components/knowledge/KnowledgePage";
import type { KnowledgeBase } from "@/lib/knowledge-helpers";
import { WithdrawalConfirmationError } from "@/lib/withdrawal-confirmation";

const fixture = vi.hoisted(() => ({
  params: { kbName: "kb:private" },
  search: new URLSearchParams(),
  router: { replace: vi.fn() },
  auth: { isAdmin: true, allowPrivateKb: true },
  kbs: [] as KnowledgeBase[],
  fetch: vi.fn(), deleteKb: vi.fn(), refresh: vi.fn(), references: vi.fn(),
}));
vi.mock("next/navigation", () => ({
  useParams: () => fixture.params, useSearchParams: () => fixture.search,
  useRouter: () => fixture.router,
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock("@/hooks/useAuthStatus", () => ({ useAuthStatus: () => fixture.auth }));
vi.mock("@/hooks/useKnowledgeBases", () => ({
  useKnowledgeBases: () => ({
    kbs: fixture.kbs, providers: [], loading: false, error: null,
    tasksByKb: {}, historyByKb: {}, refresh: fixture.refresh,
    deleteKb: fixture.deleteKb, setError: vi.fn(),
  }),
}));
vi.mock("@/lib/api", () => ({ apiFetch: fixture.fetch, apiUrl: (path: string) => path }));
vi.mock("@/lib/workspaces-api", () => ({ resourceUsage: async () => [], moveKnowledgeBase: vi.fn() }));
vi.mock("@/features/knowledge/api/catalog", () => ({
  listKnowledgeBaseReferences: fixture.references, setKnowledgeBaseVisibility: vi.fn(),
}));
vi.mock("@/components/knowledge/KnowledgeHome", () => ({ default: () => <div>Catalog</div> }));
vi.mock("@/components/knowledge/KnowledgeBaseDetail", () => ({
  default: ({ kb, onDelete }: { kb: KnowledgeBase | null; onDelete: (ref: string) => Promise<void> }) =>
    kb ? <div>
      <span>{kb.name}</span>
      <output data-testid="read-only">{String(kb.read_only)}</output>
      <button onClick={() => void onDelete(kb.ref!)}>Delete loaded detail</button>
    </div> : <div>No selection</div>,
}));

const detail: KnowledgeBase = {
  id: "kb:private", ref: "kb:private", kb_id: "private", name: "Authorized private",
  owned: false, read_only: false, visibility: "private", can_set_default: false,
};
beforeEach(() => {
  fixture.params = { kbName: "kb:private" };
  fixture.auth = { isAdmin: true, allowPrivateKb: true };
  fixture.kbs = [];
  fixture.router.replace.mockReset();
  fixture.fetch.mockReset().mockResolvedValue(Response.json(detail));
  fixture.references.mockReset().mockResolvedValue({ references: [], other_references: { total: 0, by_kind: {} } });
  fixture.deleteKb.mockReset().mockResolvedValue(undefined);
  fixture.refresh.mockReset().mockResolvedValue(undefined);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("renders an authorized stable detail absent from the intentionally narrower catalog", async () => {
  render(<KnowledgePage />);
  expect(await screen.findByText("Authorized private")).toBeTruthy();
  expect(screen.getByTestId("read-only").textContent).toBe("false");
  expect(fixture.fetch).toHaveBeenCalledWith("/api/knowledge-bases/kb%3Aprivate?resource_library=true");
  vi.spyOn(window, "confirm").mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", { name: "Delete loaded detail" }));
  await waitFor(() => expect(fixture.deleteKb).toHaveBeenCalledWith("kb:private"));
  expect(await screen.findByText("Catalog")).toBeTruthy();
});

it("deletes a KB cited by teacher paths only after confirming the counts (R1)", async () => {
  fixture.references.mockResolvedValue({ references: [], other_references: { total: 1, by_kind: { mastery_template: 1 } } });
  fixture.deleteKb
    .mockRejectedValueOnce(new WithdrawalConfirmationError("confirm", { affectedPaths: 1, affectedStudents: 4 }))
    .mockResolvedValue(undefined);
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<KnowledgePage />);
  expect(await screen.findByText("Authorized private")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Delete loaded detail" }));
  await waitFor(() => expect(fixture.deleteKb).toHaveBeenLastCalledWith("kb:private", {
    confirmed: { affectedPaths: 1, affectedStudents: 4 },
  }));
  expect(fixture.deleteKb).toHaveBeenCalledTimes(2);
  expect(confirm).toHaveBeenCalledTimes(2);
  expect(String(confirm.mock.calls[1][0])).toContain("published teacher paths use this material");
});

it("applies the effective student policy to fetched details too", async () => {
  fixture.auth = { isAdmin: false, allowPrivateKb: false };
  render(<KnowledgePage />);
  expect(await screen.findByText("Authorized private")).toBeTruthy();
  expect(screen.getByTestId("read-only").textContent).toBe("true");
});

it("does not substitute a listed namesake when detail access is denied", async () => {
  fixture.kbs = [{ ...detail, id: "kb:other", ref: "kb:other", kb_id: "other" }];
  fixture.fetch.mockResolvedValue(Response.json({ detail: "Not found" }, { status: 404 }));
  render(<KnowledgePage />);
  await waitFor(() => expect(fixture.fetch).toHaveBeenCalled());
  expect(screen.queryByText("Authorized private")).toBeNull();
});

it("ignores late authorized detail responses after navigating to another selection", async () => {
  let finish!: (response: Response) => void;
  fixture.fetch.mockImplementationOnce(() => new Promise<Response>((resolve) => { finish = resolve; }));
  const { rerender } = render(<KnowledgePage />);
  await waitFor(() => expect(fixture.fetch).toHaveBeenCalledOnce());
  fixture.params = { kbName: "kb:denied" };
  fixture.fetch.mockResolvedValue(Response.json({ detail: "Not found" }, { status: 404 }));
  rerender(<KnowledgePage />);
  await waitFor(() => expect(fixture.fetch).toHaveBeenCalledTimes(2));
  await act(async () => finish(Response.json(detail)));
  expect(screen.queryByText("Authorized private")).toBeNull();
});

it("resolves an old direct alias without requiring a catalog row", async () => {
  fixture.params = { kbName: "account:kb:old-name" };
  const { unmount } = render(<KnowledgePage />);
  await waitFor(() => expect(fixture.router.replace).toHaveBeenCalledWith(
    "/knowledge-bases/kb%3Aprivate", { scroll: false },
  ));
  // B3-P15: the detail waits for the canonical URL (the replace remounts it).
  expect(screen.queryByText("Authorized private")).toBeNull();
  // Next remounts the page for the new param; it fetches the detail afresh.
  unmount();
  fixture.params = { kbName: "kb:private" };
  fixture.fetch.mockImplementation(async () => Response.json(detail));
  render(<KnowledgePage />);
  expect(await screen.findByText("Authorized private")).toBeTruthy();
  expect(fixture.kbs).toEqual([]);
});

it("revalidates direct details on catalog refresh and clears revoked access", async () => {
  const { rerender } = render(<KnowledgePage />);
  expect(await screen.findByText("Authorized private")).toBeTruthy();
  fixture.fetch.mockResolvedValue(Response.json({ detail: "Not found" }, { status: 404 }));
  fixture.kbs = [];
  rerender(<KnowledgePage />);
  await waitFor(() => expect(fixture.fetch).toHaveBeenCalledTimes(2));
  expect(screen.queryByText("Authorized private")).toBeNull();
});
