import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

// Market closed loop, part A (spec 2026-11-11 §1, §2, §5, §6): one's progress,
// the review prompt once finished, the author's own intro page, reply notices.
const { api, auth, search, t } = vi.hoisted(() => ({
  api: {
    getTaxonomy: vi.fn(),
    getCatalogItem: vi.fn(),
    listReviews: vi.fn(),
    listFinishedUnreviewed: vi.fn(),
  },
  auth: { isAdmin: false, loading: false },
  search: { value: "" },
  t: (key: string, values?: Record<string, unknown>) =>
    key.replace(/\{\{(\w+)\}\}/g, (_: string, name: string) => String(values?.[name] ?? "")),
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t, i18n: { language: "en" } }) }));
vi.mock("@/hooks/useAuthStatus", () => ({ useAuthStatus: () => ({ ...auth, enabled: true }) }));
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

import ItemIntro from "@/components/catalog/ItemIntro";
import { ReviewItLink, useFinishedUnreviewed } from "@/components/catalog/FinishedReview";
import { openHref } from "@/components/catalog/catalog-format";
import { resetTaxonomyCache } from "@/components/catalog/useTaxonomy";
import { notificationText } from "@/components/notifications/NotificationBell";
import type { CatalogItemDetail, ItemReviews } from "@/lib/catalog-api";
import type { AppNotification } from "@/lib/notifications-api";

const item = (over: Partial<CatalogItemDetail> = {}): CatalogItemDetail => ({
  kind: "course", id: "c_1", owner_id: "u_tina", status: "listed", title: "Arithmetic", summary: "Add.",
  stages: [], subjects: [], difficulty: "", tags: [], approved_once: true, migrated: false, reason: "",
  submitted_at: null, listed_at: 1, updated_at: 1, needs_classification: false, learners: 3,
  mine: false, closed: false, can_join: true, preview: {}, study: { kind: "course", id: "c_1" },
  can_report: true, reported: false, joined: null, unavailable: null, favorited: false,
  author_view: false, progress: null, reviewed: false, can_review: false,
  ...over,
} as CatalogItemDetail);

const reviews = (over: Partial<ItemReviews> = {}): ItemReviews => ({
  average: null, count: 0, mine: null, can_review: true, reviews: [], ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
  resetTaxonomyCache();
  auth.isAdmin = false;
  search.value = "kind=course&id=c_1";
  api.getTaxonomy.mockResolvedValue({ stages: [], difficulties: [], grades: [] });
  api.listReviews.mockResolvedValue(reviews());
  api.listFinishedUnreviewed.mockResolvedValue([]);
});
afterEach(() => {
  document.body.innerHTML = "";
  window.location.hash = "";
});

// ── §1 progress ────────────────────────────────────────────

it("shows a learner's progress above Continue studying", async () => {
  api.getCatalogItem.mockResolvedValue(item({
    joined: "self", can_review: true,
    progress: { done: 2, total: 5, unit: "lessons", percent: 40, finished: false },
  }));
  render(<ItemIntro />);
  const progress = await screen.findByTestId("item-progress");
  expect(progress.textContent).toContain("2 / 5 lessons done");
  expect(within(progress).getByRole("progressbar").getAttribute("aria-valuenow")).toBe("40");
  expect(screen.queryByTestId("item-finished")).toBeNull();
  expect(screen.getByRole("link", { name: "Continue studying" }).getAttribute("href")).toBe("/tasks?course=c_1");
  expect(screen.queryByTestId("finished-banner")).toBeNull();
});

it("words a practice set's progress as submissions and the best score", async () => {
  search.value = "kind=practice_set&id=ps_1";
  api.getCatalogItem.mockResolvedValue(item({
    kind: "practice_set", id: "ps_1", study: { kind: "practice_set", id: "ps_1" }, joined: "self", reviewed: true, can_review: true,
    progress: { done: 1, total: 1, unit: "submissions", percent: 100, finished: true, submissions: 2, best_score: 3, max_score: 4 },
  }));
  render(<ItemIntro />);
  const progress = await screen.findByTestId("item-progress");
  expect(progress.textContent).toContain("Submissions: 2 · Best score 3 / 4");
  expect(within(progress).queryByRole("progressbar")).toBeNull();
  expect(screen.getByTestId("item-finished").textContent).toBe("Finished");
  expect(screen.queryByTestId("finished-banner")).toBeNull(); // already reviewed
});

// ── §2 the review prompt ───────────────────────────────────

it("asks for a review once finished, opens the rating form, and Later puts it away", async () => {
  api.getCatalogItem.mockResolvedValue(item({
    joined: "self", can_review: true,
    progress: { done: 5, total: 5, unit: "lessons", percent: 100, finished: true },
  }));
  const { unmount } = render(<ItemIntro />);
  const banner = await screen.findByTestId("finished-banner");
  expect(banner.textContent).toContain("You finished “Arithmetic”. How was it?");
  await screen.findByTestId("review-form");
  fireEvent.click(within(banner).getByRole("button", { name: "Rate it" }));
  expect(document.activeElement?.getAttribute("aria-label")).toBe("1 of 5 stars");
  expect(screen.queryByTestId("finished-banner")).toBeNull();
  unmount();

  render(<ItemIntro />);
  fireEvent.click(within(await screen.findByTestId("finished-banner")).getByRole("button", { name: "Later" }));
  expect(screen.queryByTestId("finished-banner")).toBeNull();
});

it("lists show Finished · Review it, leading to the reviews", async () => {
  api.listFinishedUnreviewed.mockResolvedValue([{ kind: "mastery_path", id: "tpl_1" }]);
  function Probe() {
    const finished = useFinishedUnreviewed();
    return (
      <ul>
        {["tpl_1", "tpl_2"].map((id) => (
          <li key={id} data-testid={id}>{finished("mastery_path", id) && <ReviewItLink kind="mastery_path" id={id} />}</li>
        ))}
      </ul>
    );
  }
  render(<Probe />);
  const link = await within(screen.getByTestId("tpl_1")).findByRole("link", { name: "Finished · Review it" });
  expect(link.getAttribute("href")).toBe("/market/item?kind=mastery_path&id=tpl_1#reviews");
  expect(within(screen.getByTestId("tpl_2")).queryByRole("link")).toBeNull();
});

// ── §5 the author's own page ───────────────────────────────

const stats = { learners: 12, recent_learners: 3, finished: 4, rating: 4.25, rating_count: 8, unanswered: 2 };

it("its author sees Open and the numbers, and no favorite or report", async () => {
  api.getCatalogItem.mockResolvedValue(item({
    mine: true, author_view: true, can_report: false, can_join: false,
    open: { kind: "course", id: "c_1" }, stats,
  }));
  render(<ItemIntro />);
  expect((await screen.findByTestId("item-open")).getAttribute("href")).toBe("/tasks?tab=courses&edit=c_1");
  expect(screen.queryByTestId("favorite-button")).toBeNull();
  expect(screen.queryByRole("button", { name: "Report this" })).toBeNull();
  const card = screen.getByTestId("item-stats");
  for (const text of ["Learners12", "Joined in the last 7 days3", "Finished it4", "Average rating4.3", "Reviews8", "Unanswered reviews2"]) {
    expect(card.textContent).toContain(text);
  }
  expect(screen.getByRole("link", { name: "Learning report" })).toBeTruthy();
});

it("other learners see neither the numbers nor Open, and keep the favorite", async () => {
  api.getCatalogItem.mockResolvedValue(item());
  render(<ItemIntro />);
  await screen.findByTestId("item-intro");
  expect(screen.queryByTestId("item-stats")).toBeNull();
  expect(screen.queryByTestId("item-open")).toBeNull();
  expect(screen.getByTestId("favorite-button")).toBeTruthy();
});

it("a knowledge base's numbers leave out who finished it", async () => {
  search.value = "kind=knowledge_base&id=kb_1";
  api.getCatalogItem.mockResolvedValue(item({
    kind: "knowledge_base", id: "kb_1", study: { kind: "knowledge_base", id: "kb_1", name: "Notes" },
    mine: true, author_view: true, open: { kind: "knowledge_base", id: "kb_1", name: "Notes" },
    stats: { ...stats, finished: null, rating: null },
  }));
  render(<ItemIntro />);
  const card = await screen.findByTestId("item-stats");
  expect(card.textContent).not.toContain("Finished it");
  expect(card.textContent).toContain("No rating yet");
});

it("Open leads each kind to where its author works on it", () => {
  expect(openHref({ kind: "practice_set", id: "ps_1" })).toBe("/practice-sets?set=ps_1");
  expect(openHref({ kind: "mastery_path", id: "tpl_1", path_id: "topic_x", workspace_id: "" })).toBe("/learning/mastery/topic_x");
  expect(openHref({ kind: "course", id: "c 1" })).toBe("/tasks?tab=courses&edit=c%201");
});

// ── §6 reply notifications ─────────────────────────────────

it("words the reply notification", () => {
  const note = { id: "n", kind: "review_replied", params: { content: "course", title: "Physics" }, link: "", read: false, created_at: 0, count: 2 } as AppNotification;
  expect(notificationText(note, t)).toBe("The author replied to your review of “Physics”");
});

it("a #reviews link opens the rating form ready to rate", async () => {
  window.location.hash = "#reviews";
  api.getCatalogItem.mockResolvedValue(item({ joined: "self", can_review: true }));
  render(<ItemIntro />);
  await screen.findByTestId("review-form");
  await waitFor(() => expect(document.activeElement?.getAttribute("aria-label")).toBe("1 of 5 stars"));
});
