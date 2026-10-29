// B3-P15 item 1: reproduces the batch-3b R1a flake ("Make private" stays
// disabled / disappears after clicking Settings on /knowledge-bases/<name>).
//
// Real chain: /knowledge-bases/B2-Extra lists the KB as `account:kb:B2-Extra`,
// so the name is not a local match, the page fetches the detail, adopts the
// stable ref `kb:<id>` and `router.replace`s to /knowledge-bases/kb%3A<id>.
// Next's layout-router keys each page segment by its param value
// (node_modules/next/dist/client/components/layout-router.js, `stateKey`), so
// that replace REMOUNTS KnowledgePage. A user (or the browser harness) who
// clicked "Settings" before the replace committed loses the tab: the new
// instance starts on Files, the in-flight references result is discarded by
// the unmounted KbSettingsSection, and "Make private" never becomes usable.
//
// Fix: the detail is withheld until the canonical ref is in the URL, and the
// active tab is carried in `?tab=` so any remount reopens it. The wrapper below
// emulates the keyed remount; router.replace is committed by the test at a
// controlled moment, references are controlled deferred promises.
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import KnowledgePage from "@/components/knowledge/KnowledgePage";
import type { KnowledgeBase } from "@/lib/knowledge-helpers";
import type { KnowledgeBaseReferenceSummary } from "@/features/knowledge/api/catalog";

const fixture = vi.hoisted(() => ({
  params: { kbName: "B2-Extra" } as { kbName?: string },
  search: new URLSearchParams(),
  router: { replace: vi.fn(), push: vi.fn() },
  auth: { isAdmin: false, isTeacher: true, allowPrivateKb: true },
  kbs: [] as KnowledgeBase[],
  fetch: vi.fn(),
  references: vi.fn(),
}));
vi.mock("next/navigation", () => ({
  useParams: () => fixture.params,
  useSearchParams: () => fixture.search,
  useRouter: () => fixture.router,
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock("@/hooks/useAuthStatus", () => ({ useAuthStatus: () => fixture.auth }));
vi.mock("@/hooks/useKnowledgeBases", () => ({
  useKnowledgeBases: () => ({
    kbs: fixture.kbs, providers: [], loading: false, error: null,
    tasksByKb: {}, historyByKb: {}, refresh: vi.fn(async () => undefined),
    deleteKb: vi.fn(), setError: vi.fn(), clearHistory: vi.fn(),
  }),
}));
vi.mock("@/lib/api", () => ({ apiFetch: fixture.fetch, apiUrl: (path: string) => path }));
vi.mock("@/lib/workspaces-api", () => ({
  resourceUsage: async () => [], moveKnowledgeBase: vi.fn(),
  listWorkspaces: async () => [], previewKnowledgeMove: vi.fn(),
}));
vi.mock("@/features/knowledge/api/catalog", () => ({
  listKnowledgeBaseReferences: fixture.references, setKnowledgeBaseVisibility: vi.fn(),
}));
vi.mock("@/components/knowledge/KnowledgeHome", () => ({ default: () => <div>Catalog</div> }));
// Leaf panels that do their own I/O are irrelevant to the race.
vi.mock("@/components/knowledge/KbFilesTab", () => ({ default: () => <div>Files tab</div> }));
vi.mock("@/components/knowledge/KbTaskLogs", () => ({ default: () => null }));
vi.mock("@/components/knowledge/KbIndexingRun", () => ({ default: () => null }));
vi.mock("@/components/knowledge/KbIndexFailureBanner", () => ({ default: () => null }));
vi.mock("@/components/knowledge/KbDocumentsSection", () => ({ default: () => null }));
vi.mock("@/components/knowledge/KbIndexVersionsSection", () => ({ default: () => null }));
vi.mock("@/components/knowledge/KbGitHubSourcesSection", () => ({ default: () => null }));
vi.mock("@/components/knowledge/KbLinkedFoldersSection", () => ({ default: () => null }));
vi.mock("@/components/knowledge/KbWebSourcesSection", () => ({ default: () => null }));
vi.mock("@/components/knowledge/KbMarginNoteDevicesSection", () => ({ default: () => null }));
// The publish-scope panel has its own fetches and its own spec.
vi.mock("@/components/publishing/PublishScopeEditor", () => ({ PublishScopeEditor: () => null }));
vi.mock("@/components/knowledge/KbKiwixArticlesSection", () => ({ default: () => null }));
vi.mock("@/components/knowledge/LightRagEmbeddingWarning", () => ({ default: () => null }));

const catalogRow: KnowledgeBase = {
  id: "account:kb:B2-Extra", kb_id: "extra1", name: "B2-Extra",
  owned: true, read_only: false, visibility: "public", can_set_default: true,
} as KnowledgeBase;
const detailBody = { ...catalogRow, ref: "kb:extra1" };
const refs: KnowledgeBaseReferenceSummary = {
  references: [], other_references: { total: 1, by_kind: { mastery_template: 1 } },
};

/** Emulates Next: a new kbName param is a new page instance; `?tab=` is not. */
function RoutedPage() {
  return <KnowledgePage key={fixture.params.kbName ?? ""} />;
}

/** Applies the last router.replace the way Next would (path => params, query => search). */
function commitLastReplace() {
  const [href] = fixture.router.replace.mock.calls.at(-1) as [string];
  const url = new URL(href, "http://localhost");
  fixture.params = { kbName: url.pathname.split("/")[2] };
  fixture.search = url.searchParams;
}

const deferred: Array<(value: KnowledgeBaseReferenceSummary) => void> = [];

beforeEach(() => {
  fixture.params = { kbName: "B2-Extra" };
  fixture.search = new URLSearchParams();
  fixture.kbs = [catalogRow];
  fixture.router.replace.mockReset();
  fixture.fetch.mockReset().mockImplementation(async () => Response.json(detailBody));
  deferred.length = 0;
  fixture.references.mockReset().mockImplementation(
    () => new Promise<KnowledgeBaseReferenceSummary>((resolve) => { deferred.push(resolve); }),
  );
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("does not offer the interactive detail until the alias URL is replaced by the canonical ref", async () => {
  const view = render(<RoutedPage />);
  await waitFor(() => expect(fixture.router.replace).toHaveBeenCalledWith(
    "/knowledge-bases/kb%3Aextra1", { scroll: false },
  ));
  // Still on the alias URL: a click here would be lost to the remount.
  // (The detail panel is a dynamic import; give it time to appear if it would.)
  await expect(
    screen.findByRole("button", { name: "Settings" }, { timeout: 500 }),
  ).rejects.toThrow();

  commitLastReplace();
  view.rerender(<RoutedPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Settings" }));
  await waitFor(() => expect(fixture.references).toHaveBeenCalledTimes(1));
  await act(async () => { for (const resolve of deferred) resolve(refs); });
  const make = await screen.findByRole("button", { name: "Make private" });
  await waitFor(() => expect((make as HTMLButtonElement).disabled).toBe(false));
});

it("keeps the Settings tab and a usable Make private button across a remount", async () => {
  fixture.params = { kbName: "kb:extra1" };
  const view = render(<RoutedPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Settings" }));
  expect(await screen.findByRole("button", { name: "Make private" })).toBeTruthy();
  // The tab is written to the URL...
  await waitFor(() => expect(fixture.router.replace).toHaveBeenLastCalledWith(
    "/knowledge-bases/kb%3Aextra1?tab=settings", { scroll: false },
  ));
  commitLastReplace();
  // ...so a fresh page instance (reload, route remount) reopens it, and the
  // references request of the instance that is actually shown settles it.
  view.unmount();
  render(<RoutedPage />);
  const make = await screen.findByRole("button", { name: "Make private" });
  await act(async () => { for (const resolve of deferred) resolve(refs); });
  await waitFor(() => expect((make as HTMLButtonElement).disabled).toBe(false));
});
