import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import fs from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

// Audit fixes, batch B: reports (§1), content that went (§2), courses in use
// (§5), connected knowledge bases (§7). Partners stay open to learners.
const { api, tasks, auth, search, t } = vi.hoisted(() => ({
  api: {
    getTaxonomy: vi.fn(),
    getCatalogItem: vi.fn(),
    listReviews: vi.fn(),
    reportItem: vi.fn(),
    reportReview: vi.fn(),
    listFlags: vi.fn(),
    handleFlags: vi.fn(),
    listItemAnnouncements: vi.fn(),
  },
  tasks: { listAssignedCourses: vi.fn() },
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
vi.mock("@/lib/tasks-api", async () => ({
  ...(await vi.importActual<typeof import("@/lib/tasks-api")>("@/lib/tasks-api")),
  ...tasks,
}));
vi.mock("@/components/catalog/ListingPanel", () => ({ ListingPanel: () => null }));
vi.mock("@/components/catalog/Announcements", () => ({ AnnouncementsPanel: () => null }));

import { FlagQueue } from "@/components/catalog/FlagQueue";
import ItemIntro from "@/components/catalog/ItemIntro";
import { JoinButton } from "@/components/catalog/JoinButton";
import { ReviewsSection } from "@/components/catalog/Reviews";
import { notificationText } from "@/components/notifications/NotificationBell";
import { LearnerCourses } from "@/components/tasks/Courses";
import { LearnerLessonDetail } from "@/components/tasks/LearnerLessonDetail";
import { resetTaxonomyCache } from "@/components/catalog/useTaxonomy";
import type { CatalogItemDetail, FlagGroup, ItemReviews } from "@/lib/catalog-api";
import type { AppNotification } from "@/lib/notifications-api";
import type { AssignedCourse, AssignedTask } from "@/lib/tasks-api";

const item = (over: Partial<CatalogItemDetail> = {}): CatalogItemDetail => ({
  kind: "practice_set", id: "ps_1", owner_id: "u_tina", status: "listed", title: "Arithmetic", summary: "Add.",
  stages: [], subjects: [], difficulty: "", tags: [], approved_once: true, migrated: false, reason: "",
  submitted_at: null, listed_at: 1, updated_at: 1, needs_classification: false, learners: 3,
  mine: false, closed: false, can_join: true, preview: {}, study: { kind: "practice_set", id: "ps_1" },
  can_report: true, reported: false, joined: null, unavailable: null,
  ...over,
} as CatalogItemDetail);

const reviews = (over: Partial<ItemReviews> = {}): ItemReviews => ({
  average: 1, count: 1, mine: null, can_review: false,
  reviews: [{
    user_id: "u_sara", author: "sara", stars: 1, text: "Buy essays", reply: null, reply_at: null,
    created_at: 1, updated_at: 1, mine: false, can_reply: false, can_delete: false, can_report: true, reported: false,
  }],
  ...over,
});

const notice = (kind: string, params: Record<string, unknown>): AppNotification =>
  ({ id: "n", kind, params, link: "", read: false, created_at: 0, count: 1 }) as AppNotification;

beforeEach(() => {
  vi.clearAllMocks();
  resetTaxonomyCache();
  auth.isAdmin = false;
  search.value = "kind=practice_set&id=ps_1";
  api.getTaxonomy.mockResolvedValue({ stages: [], difficulties: [], grades: [] });
  api.listReviews.mockResolvedValue(reviews());
  api.reportItem.mockResolvedValue({ reported: true });
  api.reportReview.mockResolvedValue({ reported: true });
});
afterEach(() => {
  document.body.innerHTML = "";
});

// ── §1 reports ─────────────────────────────────────────────

it("a learner reports content with a reason and a note, once", async () => {
  api.getCatalogItem.mockResolvedValue(item());
  render(<ItemIntro />);
  fireEvent.click(await screen.findByRole("button", { name: "Report this" }));
  const form = screen.getByTestId("report-form");
  const send = within(form).getByRole("button", { name: "Send report" });
  expect(send).toBeDisabled(); // a reason first
  fireEvent.click(within(form).getByLabelText("Other reason"));
  expect(send).toBeDisabled(); // "other" needs a few words
  fireEvent.change(within(form).getByLabelText("What is wrong? (optional)"), { target: { value: "Copied from a textbook." } });
  fireEvent.click(send);
  await waitFor(() => expect(api.reportItem).toHaveBeenCalledWith("practice_set", "ps_1", "other", "Copied from a textbook."));
  expect(await screen.findByTestId("item-reported")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Report this" })).toBeNull();
});

it("authors and administrators are not offered a report of the item", async () => {
  api.getCatalogItem.mockResolvedValue(item({ mine: true, can_report: false }));
  render(<ItemIntro />);
  await screen.findByTestId("item-intro");
  expect(screen.queryByRole("button", { name: "Report this" })).toBeNull();
});

it("a review is reported from beside it", async () => {
  render(<ReviewsSection kind="practice_set" itemId="ps_1" />);
  fireEvent.click(await screen.findByRole("button", { name: "Report the review by sara" }));
  fireEvent.click(screen.getByLabelText("Advertising"));
  fireEvent.click(screen.getByRole("button", { name: "Send report" }));
  await waitFor(() => expect(api.reportReview).toHaveBeenCalledWith("practice_set", "ps_1", "u_sara", "advertising", ""));
  expect(await screen.findByTestId("review-reported")).toBeTruthy();
});

const group = (over: Partial<FlagGroup> = {}): FlagGroup => ({
  kind: "practice_set", id: "ps_1", review_user_id: "", target: "item", title: "Arithmetic", status: "listed",
  author: "tina", count: 2, reasons: { wrong: 1, other: 1 }, first_at: 1, review: null,
  reports: [
    { reporter: "sara", reason: "wrong", note: "2 + 2 is not 5.", created_at: 1 },
    { reporter: "sam", reason: "other", note: "Copied.", created_at: 2 },
  ],
  ...over,
});

it("an administrator takes reported content down with a reason, or dismisses the reports", async () => {
  api.listFlags.mockResolvedValueOnce([group()]).mockResolvedValue([]);
  api.handleFlags.mockResolvedValue({ handled: "take_down" });
  render(<FlagQueue />);
  fireEvent.click(await screen.findByRole("button", { name: /Arithmetic/ }));
  const detail = screen.getByTestId("flag-detail");
  expect(within(detail).getByText("Reported 2 times", { exact: false })).toBeTruthy();
  expect(within(detail).getByText("2 + 2 is not 5.")).toBeTruthy();
  fireEvent.click(within(detail).getByRole("button", { name: "Take down" }));
  const confirm = within(detail).getByRole("button", { name: "Take down" });
  expect(confirm).toBeDisabled(); // the author sees why
  fireEvent.change(within(detail).getByRole("textbox"), { target: { value: "Wrong answers." } });
  fireEvent.click(confirm);
  await waitFor(() => expect(api.handleFlags).toHaveBeenCalledWith(
    expect.objectContaining({ kind: "practice_set", id: "ps_1", review_user_id: "" }), "take_down", "Wrong answers.",
  ));
  expect(await screen.findByText("No open reports.")).toBeTruthy();
});

it("a reported review is deleted or the reports dismissed; it is never taken down", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  const review = group({
    review_user_id: "u_sara", target: "review", count: 1, reasons: { advertising: 1 },
    review: { author: "sara", stars: 1, text: "Buy essays", exists: true },
  });
  api.listFlags.mockResolvedValue([review]);
  api.handleFlags.mockResolvedValue({ handled: "delete_review" });
  render(<FlagQueue />);
  fireEvent.click(await screen.findByRole("button", { name: /Arithmetic/ }));
  const detail = screen.getByTestId("flag-detail");
  expect(within(detail).getByTestId("flag-review").textContent).toContain("Buy essays");
  expect(within(detail).queryByRole("button", { name: "Take down" })).toBeNull();
  fireEvent.click(within(detail).getByRole("button", { name: "Delete review" }));
  await waitFor(() => expect(api.handleFlags).toHaveBeenCalledWith(
    expect.objectContaining({ review_user_id: "u_sara" }), "delete_review", "",
  ));
});

it("reports and their outcome read in the reader's language", () => {
  expect(notificationText(notice("report_submitted", { title: "Arithmetic" }), t)).toBe("Someone reported “Arithmetic”");
  expect(notificationText(notice("report_resolved", { title: "A", outcome: "taken_down", target: "item" }), t))
    .toBe("You reported “A”: it was taken down. Thank you.");
  expect(notificationText(notice("report_resolved", { title: "A", outcome: "review_deleted", target: "review" }), t))
    .toBe("The review of “A” you reported was removed. Thank you.");
  expect(notificationText(notice("report_resolved", { title: "A", outcome: "dismissed", target: "item" }), t))
    .toBe("You reported “A”: it was checked and kept.");
});

// ── §2 content that went ───────────────────────────────────

it("joined content that went says why instead of Go study", () => {
  const { rerender } = render(<JoinButton item={item({ joined: "self", closed: true, unavailable: "taken_down" })} onChanged={vi.fn()} />);
  expect(screen.getByTestId("item-unavailable").textContent).toBe("This was taken down. Your learning records are kept.");
  expect(screen.queryByRole("link", { name: "Go study" })).toBeNull();
  rerender(<JoinButton item={item({ joined: "self", closed: true, unavailable: "archived" })} onChanged={vi.fn()} />);
  expect(screen.getByTestId("item-unavailable").textContent).toBe("Its author withdrew this. Your learning records are kept.");
});

it("the people in it hear that it was taken down, unlisted or withdrawn", () => {
  expect(notificationText(notice("content_withdrawn", { title: "A", how: "taken_down" }), t))
    .toBe("“A” was taken down. Your learning records are kept.");
  expect(notificationText(notice("content_withdrawn", { title: "A", how: "unlisted" }), t))
    .toBe("“A” was taken out of the market. You can keep studying it.");
  expect(notificationText(notice("content_withdrawn", { title: "A", how: "archived" }), t))
    .toBe("“A” is no longer available: its author withdrew it. Your learning records are kept.");
});

const lesson = (over: Partial<AssignedTask> = {}): AssignedTask => ({
  key: "t1", task_id: "t1", kind: "practice_set", title: "Week 1", description: "", target_id: "ps_1",
  target_title: "Arithmetic", settings: {}, state: "completed", completed_at: 1,
  provenance: { kind: "shared", name: "tina" }, ...over,
} as AssignedTask);

it("a taken-down course stays under Learning, marked, with the learner's records", async () => {
  const course: AssignedCourse = {
    id: "crs_1", title: "Waves", description: "", provenance: { kind: "shared", name: "tina" }, published_at: 1,
    unavailable: "taken_down", tasks: [lesson()],
  };
  tasks.listAssignedCourses.mockResolvedValue([course]);
  render(<LearnerCourses courseId="crs_1" onOpenTask={vi.fn()} />);
  expect(await screen.findByTestId("course-unavailable")).toBeTruthy();
  expect(screen.getByText("This course was taken down. Your learning records are kept.")).toBeTruthy();
  expect(screen.getByText("Week 1")).toBeTruthy();
});

// ── §5 courses in use ──────────────────────────────────────

it("a lesson closed because its author archived what it asks for says so", () => {
  render(<LearnerLessonDetail task={lesson({ state: "closed", completed_at: null, target_archived: true })} onChanged={vi.fn()} />);
  expect(screen.getByText("The author archived this. Your own results stay here.")).toBeTruthy();
  expect(screen.queryByRole("link", { name: /Open/ })).toBeNull();
});

it("a new lesson in a joined course reads as such", () => {
  expect(notificationText(notice("task_published", { title: "Revise", teacher: "tina", course: "Waves" }), t))
    .toBe("tina added a lesson to “Waves”");
});

// ── §7 connected knowledge bases ───────────────────────────

it("the refusal to change a connected knowledge base's connection is translated everywhere", () => {
  const key = "Only administrators change where a connected knowledge base connects to.";
  for (const locale of ["en", "zh", "de", "fr", "pl", "uk"]) {
    const entries = JSON.parse(fs.readFileSync(path.join(process.cwd(), "locales", locale, "app.json"), "utf8"));
    expect(typeof entries[key], locale).toBe("string");
  }
});
