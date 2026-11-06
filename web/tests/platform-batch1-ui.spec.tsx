import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

// Platform batch 1: listing, market filters, joining, review, notifications.
const { api, auth, router, search, t } = vi.hoisted(() => ({
  api: {
    getTaxonomy: vi.fn(),
    getListing: vi.fn(),
    saveListing: vi.fn(),
    listingAction: vi.fn(),
    searchMarket: vi.fn(),
    joinItem: vi.fn(),
    leaveItem: vi.fn(),
    getReviewItem: vi.fn(),
    rejectListing: vi.fn(),
    takeDownListing: vi.fn(),
    approveListing: vi.fn(),
    getFeatured: vi.fn(),
  },
  auth: { isAdmin: false, enabled: true, loading: false },
  router: { replace: vi.fn(), push: vi.fn() },
  search: { value: "" },
  t: (key: string, values?: Record<string, unknown>) =>
    key.replace(/\{\{(\w+)\}\}/g, (_: string, name: string) => String(values?.[name] ?? "")),
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t, i18n: { language: "en" } }) }));
vi.mock("@/hooks/useAuthStatus", () => ({ useAuthStatus: () => auth }));
vi.mock("next/navigation", () => ({
  useRouter: () => router,
  usePathname: () => "/market",
  useSearchParams: () => new URLSearchParams(search.value),
}));
vi.mock("@/lib/catalog-api", async () => ({
  ...(await vi.importActual<typeof import("@/lib/catalog-api")>("@/lib/catalog-api")),
  ...api,
}));

import { ListingPanel } from "@/components/catalog/ListingPanel";
import { JoinButton } from "@/components/catalog/JoinButton";
import MarketBrowser from "@/components/catalog/MarketBrowser";
import { ReviewDetail } from "@/components/catalog/ReviewDesk";
import { parseMarketQuery } from "@/components/catalog/market-query";
import { toggleFacet } from "@/components/catalog/FacetSidebar";
import { indexTaxonomy, resetTaxonomyCache } from "@/components/catalog/useTaxonomy";
import { notificationText } from "@/components/notifications/NotificationBell";
import { marketQueryString, type AuthorListing, type CatalogItemDetail, type Taxonomy } from "@/lib/catalog-api";
import type { AppNotification } from "@/lib/notifications-api";

const taxonomy: Taxonomy = {
  stages: [
    {
      id: "stage.university", names: { zh: "大学", en: "University" },
      subjects: [{ id: "stage.university.engineering", names: { zh: "工学", en: "Engineering" },
        children: [{ id: "stage.university.engineering.computer", names: { zh: "计算机类", en: "Computing" } }] }],
    },
    { id: "stage.primary", names: { zh: "小学", en: "Primary school" },
      subjects: [{ id: "stage.primary.math", names: { zh: "数学", en: "Mathematics" }, children: [] }] },
  ],
  difficulties: [{ id: "beginner", names: { zh: "入门", en: "Beginner" } }],
};

const listing = (over: Partial<AuthorListing> = {}): AuthorListing => ({
  kind: "practice_set", id: "set_1", owner_id: "u_t", status: "draft", title: "Waves", summary: "All about waves",
  stages: ["stage.university"], subjects: ["stage.university.engineering"], difficulty: "beginner", tags: [],
  approved_once: false, migrated: false, reason: "", submitted_at: null, listed_at: null, updated_at: null,
  needs_classification: false, learners: 4, not_ready: "", ...over,
});

beforeEach(() => {
  for (const mock of Object.values(api)) mock.mockReset();
  router.replace.mockReset();
  resetTaxonomyCache();
  api.getTaxonomy.mockResolvedValue(taxonomy);
  api.getFeatured.mockResolvedValue({ popular: [], top_rated: [] });
  Object.assign(auth, { isAdmin: false, enabled: true, loading: false });
  search.value = "";
});

it.each([
  ["draft", false, ["Submit for review"]],
  ["rejected", false, ["Submit for review"]],
  ["taken_down", true, ["Submit for review"]],
  ["pending", false, ["Withdraw request"]],
  ["listed", true, ["Take out of the market"]],
  ["unlisted", true, ["Put back"]],
  ["unlisted", false, ["Submit for review"]],
] as const)("offers the buttons of a %s listing (approved before: %s)", async (status, approvedOnce, expected) => {
  api.getListing.mockResolvedValue(listing({ status, approved_once: approvedOnce }));
  render(<ListingPanel kind="practice_set" itemId="set_1" />);
  await screen.findByTestId("listing-status");
  for (const name of ["Submit for review", "Withdraw request", "Take out of the market", "Put back"]) {
    if (expected.includes(name as never)) expect(screen.getByRole("button", { name })).toBeTruthy();
    else expect(screen.queryByRole("button", { name })).toBeNull();
  }
  expect(screen.getByText("4 learners")).toBeTruthy();
});

it("shows why it was turned down, blocks submitting what is not ready, and lets administrators list at once", async () => {
  api.getListing.mockResolvedValue(listing({ status: "rejected", reason: "Too short", not_ready: "Publish the practice set first." }));
  const { unmount } = render(<ListingPanel kind="practice_set" itemId="set_1" />);
  expect(await screen.findByText("Not approved: Too short")).toBeTruthy();
  expect(screen.getByText("Publish the practice set first.")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Submit for review" })).toBeDisabled();
  unmount();

  auth.isAdmin = true;
  api.getListing.mockResolvedValue(listing({ status: "none" }));
  api.saveListing.mockResolvedValue(listing({ status: "draft" }));
  api.listingAction.mockResolvedValue(listing({ status: "listed" }));
  render(<ListingPanel kind="practice_set" itemId="set_1" />);
  fireEvent.click(await screen.findByRole("button", { name: "List now" }));
  await waitFor(() => expect(api.listingAction).toHaveBeenCalledWith("practice_set", "set_1", "submit"));
  // A listing never saved is saved with the form before it is submitted.
  expect(api.saveListing).toHaveBeenCalledWith("practice_set", "set_1", expect.objectContaining({ title: "Waves" }));
});

it("is offered to every learner who authors something", async () => {
  // Spec 2026-11-05: listing is not reserved to a role any more.
  api.getListing.mockResolvedValue(listing({ kind: "book", id: "b1" }));
  render(<ListingPanel kind="book" itemId="b1" />);
  expect(await screen.findByTestId("listing-status")).toBeTruthy();
  expect(api.getListing).toHaveBeenCalledWith("book", "b1");
});

it("keeps the market's conditions in the address", () => {
  const query = parseMarketQuery(new URLSearchParams("q=wave&stage=stage.university&kind=book,course&sort=popular&page=2"));
  expect(query).toMatchObject({ q: "wave", stage: ["stage.university"], kind: ["book", "course"], sort: "popular", page: 2 });
  expect(marketQueryString(query)).toBe("q=wave&stage=stage.university&kind=book%2Ccourse&sort=popular&page=2");
  // Dropping a stage drops the subjects chosen under it, and starts again on page 1.
  const index = indexTaxonomy(taxonomy);
  const next = toggleFacet(
    { ...query, stage: ["stage.university", "stage.primary"], subject: ["stage.university.engineering.computer", "stage.primary.math"] },
    "stage", "stage.university", index,
  );
  expect(next.stage).toEqual(["stage.primary"]);
  expect(next.subject).toEqual(["stage.primary.math"]);
  expect(next.page).toBe(1);
});

const page = (items = 1) => ({
  total: items, page: 1, page_size: 24,
  items: Array.from({ length: items }, (_, i) => ({
    ...listing({ status: "listed", kind: "book", id: `b${i}` }), title: `Book ${i}`, author: "tina", author_role: "teacher", joined: null,
  })),
  facets: {
    stages: { "stage.university": 3, "stage.primary": 1 }, subjects: { "stage.university.engineering": 2 },
    kinds: { book: 3 }, difficulties: { beginner: 1 }, tags: [{ tag: "waves", count: 2 }],
    authors: [{ id: "u_t", name: "tina", role: "teacher", count: 3 }],
  },
});

it("filters from the sidebar with counts, shows the chosen conditions and clears them", async () => {
  search.value = "stage=stage.university&kind=book";
  api.searchMarket.mockResolvedValue(page());
  render(<MarketBrowser />);
  await screen.findByText("Book 0");
  expect(api.searchMarket).toHaveBeenCalledWith(expect.objectContaining({ stage: ["stage.university"], kind: ["book"] }));
  const sidebar = screen.getByTestId("facet-sidebar");
  const primary = await within(sidebar).findByRole("checkbox", { name: /Primary school/ });
  expect(primary.textContent).toContain("1");
  fireEvent.click(primary);
  expect(router.replace).toHaveBeenLastCalledWith("/market?stage=stage.university%2Cstage.primary&kind=book");
  const chips = screen.getByTestId("active-filters");
  fireEvent.click(within(chips).getByRole("button", { name: "Remove filter Book" }));
  expect(router.replace).toHaveBeenLastCalledWith("/market?stage=stage.university");
  fireEvent.click(within(chips).getByRole("button", { name: "Clear all" }));
  expect(router.replace).toHaveBeenLastCalledWith("/market");
});

it("offers to clear the conditions when nothing matches", async () => {
  search.value = "tag=nothing";
  api.searchMarket.mockResolvedValue({ ...page(0), items: [] });
  render(<MarketBrowser />);
  expect(await screen.findByText("Nothing matches these conditions.")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
  expect(router.replace).toHaveBeenLastCalledWith("/market");
});

const detail = (over: Partial<CatalogItemDetail> = {}): CatalogItemDetail => ({
  ...listing({ status: "listed" }), kind: "practice_set", id: "set_1", author: "tina", author_role: "teacher", joined: null,
  mine: false, closed: false, can_join: true, preview: {}, study: { kind: "practice_set", id: "set_1" }, ...over,
});

it("joins, then offers to go study and to leave", async () => {
  api.joinItem.mockResolvedValue({ joined: "self" });
  const changed = vi.fn();
  const { rerender } = render(<JoinButton item={detail()} onChanged={changed} />);
  fireEvent.click(screen.getByRole("button", { name: "Join" }));
  await waitFor(() => expect(api.joinItem).toHaveBeenCalledWith("practice_set", "set_1"));
  expect(changed).toHaveBeenCalled();

  rerender(<JoinButton item={detail({ joined: "self" })} onChanged={changed} />);
  expect(screen.getByRole("link", { name: "Go study" }).getAttribute("href")).toBe("/practice-sets?set=set_1");
  vi.spyOn(window, "confirm").mockReturnValue(true);
  api.leaveItem.mockResolvedValue({ joined: null });
  fireEvent.click(screen.getByRole("button", { name: "Leave" }));
  await waitFor(() => expect(api.leaveItem).toHaveBeenCalledWith("practice_set", "set_1"));
});

it("shows no join button where the account cannot join, nor to the author", () => {
  const { rerender, container } = render(<JoinButton item={detail({ can_join: false })} onChanged={vi.fn()} />);
  expect(screen.queryByRole("button", { name: "Join" })).toBeNull();
  // Platform batch 2 §6: every role may join; ``can_join`` false simply shows nothing.
  expect(screen.queryByText("Only student accounts can join this for now.")).toBeNull();
  rerender(<JoinButton item={detail({ mine: true, can_join: false })} onChanged={vi.fn()} />);
  expect(container.innerHTML).toBe("");
});

it("asks for a reason before rejecting a listing", async () => {
  api.getReviewItem.mockResolvedValue({ ...listing({ status: "pending" }), author: "tina", author_role: "teacher",
    preview: { question_count: 1, types: { choice: 1 }, total_points: 2,
      questions: [{ question: "2+2?", question_type: "choice", options: { A: "4" }, correct_answer: "A", explanation: "", points: 2 }] },
    events: [] });
  api.rejectListing.mockResolvedValue(listing({ status: "rejected" }));
  const done = vi.fn();
  render(<ReviewDetail entry={listing({ status: "pending" })} onDone={done} />);
  // The reviewer reads every question with its answer.
  expect(await screen.findByText("2+2?")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Reject" }));
  const confirm = screen.getByRole("button", { name: "Reject" });
  expect(confirm).toBeDisabled();
  fireEvent.change(screen.getByRole("textbox", { name: "Why is it not approved?" }), { target: { value: "  " } });
  expect(confirm).toBeDisabled();
  fireEvent.change(screen.getByRole("textbox", { name: "Why is it not approved?" }), { target: { value: " Too short " } });
  fireEvent.click(confirm);
  await waitFor(() => expect(api.rejectListing).toHaveBeenCalledWith("practice_set", "set_1", "Too short"));
  expect(done).toHaveBeenCalled();
});

const note = (kind: string, params: Record<string, unknown>): AppNotification =>
  ({ id: "n", kind, params, link: "", count: 1, created_at: 1, read: false }) as AppNotification;

it("words the four listing notifications", () => {
  expect(notificationText(note("listing_submitted", { title: "Waves" }), t)).toBe("“Waves” is waiting for review");
  expect(notificationText(note("listing_approved", { title: "Waves" }), t)).toBe("“Waves” is in the market");
  expect(notificationText(note("listing_rejected", { title: "Waves", reason: "Too short" }), t))
    .toBe("“Waves” was not approved: Too short");
  expect(notificationText(note("listing_taken_down", { title: "Waves", reason: "Outdated" }), t))
    .toBe("“Waves” was taken out of the market: Outdated");
});
