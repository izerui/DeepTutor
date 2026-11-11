import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

// Market closed loop, part B (spec 2026-11-11 §3, §4, §7): next to study / related,
// trying a book's first chapter and a practice set's first questions, favorites that changed.
const { api, search, t } = vi.hoisted(() => ({
  api: {
    getTaxonomy: vi.fn(),
    getCatalogItem: vi.fn(),
    listReviews: vi.fn(),
    listNextItems: vi.fn(),
    getBookTrial: vi.fn(),
    listUpdatedFavorites: vi.fn(),
  },
  search: { value: "" },
  t: (key: string, values?: Record<string, unknown>) =>
    key.replace(/\{\{(\w+)\}\}/g, (_: string, name: string) => String(values?.[name] ?? "")),
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t, i18n: { language: "en" } }) }));
vi.mock("@/hooks/useAuthStatus", () => ({ useAuthStatus: () => ({ isAdmin: false, loading: false, enabled: true }) }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/market/item",
  useSearchParams: () => new URLSearchParams(search.value),
}));
vi.mock("@/lib/catalog-api", async () => ({
  ...(await vi.importActual<typeof import("@/lib/catalog-api")>("@/lib/catalog-api")),
  ...api,
}));
vi.mock("@/components/catalog/ListingPanel", () => ({ ListingPanel: () => null }));
vi.mock("@/components/catalog/Announcements", () => ({ AnnouncementsPanel: () => null }));

import BookTrial from "@/components/catalog/BookTrial";
import { CatalogPreview } from "@/components/catalog/CatalogPreview";
import { ItemCard } from "@/components/catalog/ItemCard";
import ItemIntro from "@/components/catalog/ItemIntro";
import { resetTaxonomyCache } from "@/components/catalog/useTaxonomy";
import { FavoriteUpdates } from "@/components/home/LearnerHome";
import type { BookTrial as Trial, CatalogEntry, CatalogItemDetail, ItemReviews } from "@/lib/catalog-api";

const entry = (over: Partial<CatalogEntry> = {}): CatalogEntry => ({
  kind: "practice_set", id: "ps_2", owner_id: "u_tom", status: "listed", title: "Fractions", summary: "",
  stages: [], subjects: [], difficulty: "", tags: [], approved_once: true, migrated: false, reason: "",
  submitted_at: null, listed_at: 1, updated_at: 1, needs_classification: false, learners: 2,
  author: "Tom", author_role: "teacher", joined: null, favorited: false,
  ...over,
});

const item = (over: Partial<CatalogItemDetail> = {}): CatalogItemDetail => ({
  ...entry({ kind: "book", id: "bk_1", owner_id: "u_tina", title: "Waves", author: "Tina" }),
  mine: false, closed: false, can_join: true, preview: {}, study: { kind: "book", id: "bk_1" },
  can_report: true, reported: false, unavailable: null, author_view: false, progress: null,
  reviewed: false, can_review: false, can_try: false,
  ...over,
} as CatalogItemDetail);

const reviews = (): ItemReviews => ({ average: null, count: 0, mine: null, can_review: false, reviews: [] });

beforeEach(() => {
  vi.clearAllMocks();
  resetTaxonomyCache();
  search.value = "kind=book&id=bk_1";
  api.getTaxonomy.mockResolvedValue({ stages: [], difficulties: [], grades: [] });
  api.listReviews.mockResolvedValue(reviews());
  api.listNextItems.mockResolvedValue([]);
  api.listUpdatedFavorites.mockResolvedValue([]);
});
afterEach(() => {
  document.body.innerHTML = "";
});

// ── §3 next to study ───────────────────────────────────────

it("shows Related below the intro for someone who has not joined, as cards", async () => {
  api.getCatalogItem.mockResolvedValue(item());
  api.listNextItems.mockResolvedValue([entry(), entry({ id: "ps_3", title: "Decimals" })]);
  render(<ItemIntro />);
  const row = await screen.findByTestId("item-next");
  expect(api.listNextItems).toHaveBeenCalledWith("book", "bk_1");
  expect(within(row).getByRole("heading").textContent).toBe("Related");
  expect(within(row).getAllByTestId("market-card").map((c) => within(c).getAllByRole("link")[0].textContent))
    .toEqual(["Fractions", "Decimals"]);
});

it("calls the row Next to study once joined, and hides it when empty", async () => {
  api.getCatalogItem.mockResolvedValue(item({ joined: "self" }));
  api.listNextItems.mockResolvedValue([entry()]);
  const { unmount } = render(<ItemIntro />);
  const row = await screen.findByTestId("item-next");
  expect(within(row).getByRole("heading").textContent).toBe("Next to study");
  unmount();

  api.listNextItems.mockResolvedValue([]);
  render(<ItemIntro />);
  await screen.findByTestId("item-intro");
  await waitFor(() => expect(api.listNextItems).toHaveBeenCalledTimes(2));
  expect(screen.queryByTestId("item-next")).toBeNull();
});

// ── §4 trying first ────────────────────────────────────────

it("offers Try reading on the first chapter with pages only, when the book can be tried", () => {
  const preview = { chapters: [{ id: "c0", title: "Empty", pages: 0 }, { id: "c1", title: "Nodes", pages: 2 }, { id: "c2", title: "Beats", pages: 1 }] };
  const { unmount } = render(<CatalogPreview kind="book" itemId="bk_1" preview={preview} canTry />);
  const links = screen.getAllByTestId("try-reading");
  expect(links).toHaveLength(1);
  expect(links[0].closest("li")?.textContent).toContain("Nodes");
  expect(links[0].getAttribute("href")).toBe("/market/try?id=bk_1");
  unmount();

  render(<CatalogPreview kind="book" itemId="bk_1" preview={preview} />);
  expect(screen.queryByTestId("try-reading")).toBeNull();
});

it("passes can_try from the intro to the chapter list", async () => {
  api.getCatalogItem.mockResolvedValue(item({ can_try: true, preview: { chapters: [{ id: "c1", title: "Nodes", pages: 2 }] } }));
  render(<ItemIntro />);
  expect((await screen.findByTestId("try-reading")).textContent).toBe("Try reading");
});

it("shows a practice set's sample questions with their options and nothing to answer", () => {
  render(
    <CatalogPreview kind="practice_set" itemId="ps_1" preview={{
      question_count: 5, total_points: 10, types: { choice: 5 },
      sample_questions: [{ question: "2 + 2 = ?", question_type: "choice", options: { A: "3", B: "4" } }],
    }} />,
  );
  const samples = screen.getByTestId("sample-questions");
  expect(samples.textContent).toContain("Sample questions");
  expect(samples.textContent).toContain("2 + 2 = ?");
  expect(samples.textContent).toContain("B. 4");
  expect(samples.textContent).not.toContain("Answer");
  expect(within(samples).queryByRole("button")).toBeNull();
  expect(within(samples).queryByRole("radio")).toBeNull();
  expect(within(samples).queryByRole("textbox")).toBeNull();
});

const trial = (): Trial => ({
  book: { id: "bk_1", title: "Waves", language: "en" },
  chapter: { id: "c1", title: "Nodes" },
  chapters: [{ id: "c1", title: "Nodes", trial: true }, { id: "c2", title: "Beats", trial: false }],
  pages: [{
    id: "pg_1", chapter_id: "c1", title: "Standing waves", order: 0, status: "ready",
    blocks: [{ id: "b1", type: "text", status: "ready", title: "", payload: { body: "Nodes stay still." } }],
  }],
});

it("reads the first chapter read only and says Join to read for the others", async () => {
  search.value = "id=bk_1";
  api.getBookTrial.mockResolvedValue(trial());
  render(<BookTrial />);
  const view = await screen.findByTestId("book-trial");
  expect(api.getBookTrial).toHaveBeenCalledWith("bk_1");
  expect(view.textContent).toContain("You are reading the first chapter. Join to read the whole book.");
  expect(await screen.findByText(/Nodes stay still\./)).toBeTruthy();
  expect(screen.getAllByTestId("trial-page")).toHaveLength(1);
  expect(screen.queryByTestId("join-to-read")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Beats" }));
  const locked = screen.getByTestId("join-to-read");
  expect(locked.textContent).toContain("Join to read");
  expect(within(locked).getByRole("link").getAttribute("href")).toBe("/market/item?kind=book&id=bk_1");
  // Read only: no block toolbar (edit, regenerate, delete).
  expect(screen.queryByRole("button", { name: "Edit text" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Regenerate" })).toBeNull();
});

it("shows the server's message when the book cannot be tried", async () => {
  search.value = "id=bk_gone";
  const { CatalogApiError } = await vi.importActual<typeof import("@/lib/catalog-api")>("@/lib/catalog-api");
  api.getBookTrial.mockRejectedValue(new CatalogApiError("Not found.", 404));
  render(<BookTrial />);
  expect((await screen.findByRole("alert")).textContent).toBe("Not found.");
});

// ── §7 favorites that changed ──────────────────────────────

it("marks a favorite card Has updates", () => {
  const { unmount } = render(<ItemCard entry={entry({ favorited: true, updated: true })} nameOf={(id) => id} />);
  expect(screen.getByTestId("card-updated").textContent).toBe("Has updates");
  unmount();
  render(<ItemCard entry={entry({ favorited: true, updated: false })} nameOf={(id) => id} />);
  expect(screen.queryByTestId("card-updated")).toBeNull();
});

it("lists favorites with updates on the home page, hidden when none", async () => {
  api.listUpdatedFavorites.mockResolvedValue([entry({ updated: true, favorited: true })]);
  const { unmount } = render(<FavoriteUpdates />);
  const section = await screen.findByTestId("home-favorite-updates");
  expect(section.textContent).toContain("Favorites with updates");
  expect(within(section).getByRole("link", { name: /Fractions/ }).getAttribute("href")).toBe("/market/item?kind=practice_set&id=ps_2");
  unmount();

  api.listUpdatedFavorites.mockResolvedValue([]);
  render(<FavoriteUpdates />);
  await waitFor(() => expect(api.listUpdatedFavorites).toHaveBeenCalledTimes(2));
  expect(screen.queryByTestId("home-favorite-updates")).toBeNull();
});
