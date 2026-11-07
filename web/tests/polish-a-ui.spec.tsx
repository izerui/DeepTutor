import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

// Polish batch, part A: what one joined that went (§1), authors hear of joins
// and see the last week (§2), deleting one's own account (§3), empty states (§4).
const { catalog, sets, tasks, profile, authApi, auth, search, t } = vi.hoisted(() => ({
  catalog: {
    getTaxonomy: vi.fn(),
    searchMarket: vi.fn(),
    getFeatured: vi.fn(),
    getAuthor: vi.fn(),
    listMyListings: vi.fn(),
    getReviewItem: vi.fn(),
    relistHandedOver: vi.fn(),
  },
  sets: { listAssigned: vi.fn(), listMySets: vi.fn(), takeSet: vi.fn(), submitSet: vi.fn() },
  tasks: { listAssignedCourses: vi.fn(), listAssignedTasks: vi.fn(), listMyCourses: vi.fn(), listMyTasks: vi.fn() },
  profile: { deleteOwnAccount: vi.fn() },
  authApi: { logout: vi.fn() },
  auth: { isAdmin: false, enabled: true, loading: false },
  search: { value: "" },
  t: (key: string, values?: Record<string, unknown>) =>
    key.replace(/\{\{(\w+)\}\}/g, (_: string, name: string) => String(values?.[name] ?? "")),
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t, i18n: { language: "en" } }) }));
vi.mock("@/hooks/useAuthStatus", () => ({ useAuthStatus: () => auth }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/market",
  useSearchParams: () => new URLSearchParams(search.value),
}));
vi.mock("@/lib/catalog-api", async () => ({
  ...(await vi.importActual<typeof import("@/lib/catalog-api")>("@/lib/catalog-api")),
  ...catalog,
}));
vi.mock("@/lib/practice-sets-api", async () => ({
  ...(await vi.importActual<typeof import("@/lib/practice-sets-api")>("@/lib/practice-sets-api")),
  ...sets,
}));
vi.mock("@/lib/tasks-api", async () => ({
  ...(await vi.importActual<typeof import("@/lib/tasks-api")>("@/lib/tasks-api")),
  ...tasks,
}));
vi.mock("@/lib/profile-api", async () => ({
  ...(await vi.importActual<typeof import("@/lib/profile-api")>("@/lib/profile-api")),
  ...profile,
}));
vi.mock("@/lib/auth", async () => ({
  ...(await vi.importActual<typeof import("@/lib/auth")>("@/lib/auth")),
  ...authApi,
}));
vi.mock("@/components/catalog/ListingPanel", () => ({ ListingPanel: () => null }));
vi.mock("@/components/catalog/Announcements", () => ({ AnnouncementsPanel: () => null }));

import AuthorProfile from "@/components/catalog/AuthorProfile";
import MarketBrowser from "@/components/catalog/MarketBrowser";
import { MyListings } from "@/components/catalog/MyListings";
import { ReviewDetail } from "@/components/catalog/ReviewDesk";
import { resetTaxonomyCache } from "@/components/catalog/useTaxonomy";
import { notificationText } from "@/components/notifications/NotificationBell";
import PracticeSetsHub from "@/components/practice-sets/PracticeSetsHub";
import { TakeSet } from "@/components/practice-sets/TakeSet";
import { DeleteAccountCard } from "@/components/profile/DeleteAccountCard";
import { TopicAtlas } from "@/components/space/learning/TopicAtlas";
import { CourseAuthorView, LearnerCourses } from "@/components/tasks/Courses";
import TasksHub from "@/components/tasks/TasksHub";
import type { CatalogEntry } from "@/lib/catalog-api";
import type { AppNotification } from "@/lib/notifications-api";
import type { AssignedSet, TakeView } from "@/lib/practice-sets-api";
import type { AssignedCourse } from "@/lib/tasks-api";

const entry = (over: Partial<CatalogEntry> = {}): CatalogEntry => ({
  kind: "practice_set", id: "ps_1", owner_id: "u_tina", status: "listed", title: "Arithmetic", summary: "Add.",
  stages: [], subjects: [], difficulty: "", tags: [], approved_once: true, migrated: false, reason: "",
  submitted_at: null, listed_at: 1, updated_at: 1, needs_classification: false, learners: 3,
  ...over,
});

const assignedSet = (over: Partial<AssignedSet> = {}): AssignedSet => ({
  id: "ps_1", title: "Week 1", description: "", status: "published", current_revision: 1, reveal: "after_submit",
  attempts_limit: null, score_mode: "best", created_at: 1, updated_at: 1,
  teacher: "tina", submissions: 1, best_score: 2, max_score: 2, unavailable: null, unlisted: false,
  ...over,
} as AssignedSet);

const notice = (kind: string, params: Record<string, unknown>, count = 1): AppNotification =>
  ({ id: "n", kind, params, link: "", read: false, created_at: 0, count }) as AppNotification;

beforeEach(() => {
  vi.clearAllMocks();
  resetTaxonomyCache();
  auth.isAdmin = false;
  search.value = "";
  catalog.getTaxonomy.mockResolvedValue({ stages: [], difficulties: [] });
  catalog.getFeatured.mockResolvedValue({ popular: [], top_rated: [] });
});

// ── §1 ────────────────────────────────────────────────────

it("keeps a taken-down set among those joined, with why and its results instead of Start", async () => {
  search.value = "tab=joined";
  sets.listMySets.mockResolvedValue([]);
  sets.listAssigned.mockResolvedValue([
    assignedSet({ id: "gone", title: "Gone", unavailable: "taken_down" }),
    assignedSet({ id: "out", title: "Out", unlisted: true, submissions: 0, best_score: null }),
  ]);
  render(<PracticeSetsHub />);
  const gone = (await screen.findByText("Gone")).closest("li") as HTMLElement;
  expect(within(gone).getByTestId("withdrawn-badge").textContent).toBe("Taken down");
  expect(within(gone).getByTestId("set-unavailable").textContent).toBe("This was taken down. Your learning records are kept.");
  expect(within(gone).queryByRole("button", { name: /Start|Answer again/ })).toBeNull();
  expect(within(gone).getByRole("button", { name: "View my results" })).toBeTruthy();
  const out = screen.getByText("Out").closest("li") as HTMLElement;
  expect(within(out).getByTestId("withdrawn-badge").textContent).toBe("Taken out of the market by its author");
  expect(within(out).getByRole("button", { name: "Start" })).toBeTruthy();
});

it("shows one's last attempt of a set that went read-only, with nothing to submit", async () => {
  const view: TakeView = {
    ...assignedSet(), answers_revealed: true, attempts_left: 0, class_average: null,
    questions: [{ question_id: "q1", question: "2 + 2 = ?", question_type: "choice", options: { A: "3", B: "4" }, points: 2,
      correct_answer: "B", explanation: "" }],
    submissions: [{ id: "s1", revision: 1, submitted_at: 1, score: 2, max_score: 2 }],
    latest_revision: 1,
    latest_results: [{ question_id: "q1", result: "correct", points: 2, max_points: 2, answer: "B" }],
    unavailable: "archived",
  } as TakeView;
  sets.takeSet.mockResolvedValue(view);
  render(<TakeSet setId="ps_1" />);
  expect((await screen.findByTestId("take-unavailable")).textContent).toBe(
    "Its author withdrew this. Your learning records are kept.",
  );
  expect(screen.getByText("Score 2 / 2")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Submit" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Answer again" })).toBeNull();
});

it("marks a course its author took out of the market, still studied", async () => {
  const course: AssignedCourse = {
    id: "c1", title: "Algebra", description: "", provenance: { kind: "shared", name: "tina" }, published_at: 1,
    unavailable: null, unlisted: true, tasks: [],
  } as AssignedCourse;
  tasks.listAssignedCourses.mockResolvedValue([course]);
  render(<LearnerCourses courseId={null} onOpenTask={() => undefined} />);
  expect((await screen.findByTestId("withdrawn-badge")).textContent).toBe("Taken out of the market by its author");
  expect(screen.queryByTestId("course-unavailable")).toBeNull();
});

it("lists paths one joined that went, read-only, with how far one got", () => {
  render(
    <TopicAtlas topics={[]} loading={false} error={null} onRetry={() => undefined}
      unavailableTopics={[{ path_id: "assigned_t1", template_id: "t1", name: "Calculus", unavailable: "taken_down",
        objectives: 3, mastered: 1, updated_at: 1 }]} />,
  );
  const card = screen.getByTestId("unavailable-topic");
  expect(within(card).getByText("Taken down")).toBeTruthy();
  expect(within(card).getByText("1 of 3 objectives mastered")).toBeTruthy();
  expect(within(card).queryByRole("link")).toBeNull();
});

// ── §2 ────────────────────────────────────────────────────

it("words join notifications per item and day, and the author-left withdrawal", () => {
  expect(notificationText(notice("content_joined", { title: "Week 1" }), t)).toBe("Someone joined “Week 1”");
  expect(notificationText(notice("content_joined", { title: "Week 1" }, 3), t)).toBe("3 people joined “Week 1” today");
  expect(notificationText(notice("content_withdrawn", { title: "Week 1", how: "author_deleted" }), t)).toBe(
    "“Week 1” was taken out of the market because its author left. You can keep studying it.",
  );
});

it("shows who joined in the last 7 days on the author page and in My listings", async () => {
  search.value = "id=u_tina";
  catalog.getAuthor.mockResolvedValue({
    id: "u_tina", name: "tina", role: "learner", learners: 5, recent_learners: 2, item_count: 1, rating: null,
    rating_count: 0, items: [entry({ recent_learners: 2 })],
  });
  render(<AuthorProfile />);
  expect((await screen.findByTestId("author-recent-learners")).textContent).toBe("2");
  expect(screen.getByTestId("card-recent-learners").textContent).toBe("2 joined in the last 7 days");

  catalog.listMyListings.mockResolvedValue([entry({ recent_learners: 0 })]);
  render(<MyListings />);
  expect((await screen.findByTestId("listing-recent-learners")).textContent).toBe("0 joined in the last 7 days");
});

// ── §3 ────────────────────────────────────────────────────

it("deletes one's own account after the password, then signs out", async () => {
  profile.deleteOwnAccount.mockRejectedValueOnce(new Error("The password is incorrect.")).mockResolvedValueOnce(undefined);
  authApi.logout.mockResolvedValue(undefined);
  const replace = vi.fn();
  vi.stubGlobal("location", { ...window.location, replace });
  render(<DeleteAccountCard />);
  fireEvent.click(screen.getByRole("button", { name: "Delete account" }));
  const submit = screen.getByRole("button", { name: "Delete my account" }) as HTMLButtonElement;
  expect(submit.disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("Enter your password to confirm"), { target: { value: "nope" } });
  fireEvent.click(submit);
  expect((await screen.findByRole("alert")).textContent).toBe("The password is incorrect.");
  fireEvent.change(screen.getByLabelText("Enter your password to confirm"), { target: { value: "password1234" } });
  fireEvent.click(screen.getByRole("button", { name: "Delete my account" }));
  await waitFor(() => expect(replace).toHaveBeenCalled());
  expect(profile.deleteOwnAccount).toHaveBeenLastCalledWith("password1234");
  expect(authApi.logout).toHaveBeenCalled();
  vi.unstubAllGlobals();
});

it("lets an administrator put back content handed over when its author left", async () => {
  auth.isAdmin = true;
  const handed = entry({ status: "unlisted", author: "", author_role: "admin" });
  catalog.getReviewItem.mockResolvedValue({ ...handed, preview: {}, events: [] });
  catalog.relistHandedOver.mockResolvedValue({ ...handed, status: "listed" });
  const done = vi.fn();
  render(<ReviewDetail entry={handed} onDone={done} canClassify />);
  fireEvent.click(await screen.findByRole("button", { name: "Put back in the market" }));
  await waitFor(() => expect(catalog.relistHandedOver).toHaveBeenCalledWith("practice_set", "ps_1"));
  expect(done).toHaveBeenCalled();
});

it("offers no putting back for an author's own unlisted item", async () => {
  const own = entry({ status: "unlisted", author: "tina", author_role: "learner" });
  catalog.getReviewItem.mockResolvedValue({ ...own, preview: {}, events: [] });
  render(<ReviewDetail entry={own} onDone={() => undefined} canClassify />);
  await screen.findByTestId("review-detail");
  expect(screen.queryByRole("button", { name: "Put back in the market" })).toBeNull();
});

// ── §4 empty states ───────────────────────────────────────

it("invites the first content when the market is empty", async () => {
  catalog.searchMarket.mockResolvedValue({ total: 0, page: 1, page_size: 24, items: [], facets: {
    stages: {}, subjects: {}, kinds: {}, difficulties: {}, tags: [], authors: [] } });
  render(<MarketBrowser />);
  const empty = await screen.findByTestId("market-empty");
  expect(within(empty).getByRole("link", { name: "Create a course" }).getAttribute("href")).toBe("/tasks?tab=courses");
  expect(within(empty).getByRole("link", { name: "Create a practice set" }).getAttribute("href")).toBe("/practice-sets?tab=mine");
});

it("leads an empty Learning tab and an empty joined-sets list to the market", async () => {
  tasks.listAssignedTasks.mockResolvedValue([]);
  tasks.listAssignedCourses.mockResolvedValue([]);
  render(<TasksHub />);
  const learning = await screen.findByTestId("learning-courses-empty");
  expect(within(learning).getByRole("link", { name: "Find a course in the market" }).getAttribute("href")).toBe("/market?kind=course");

  search.value = "tab=joined";
  sets.listMySets.mockResolvedValue([]);
  sets.listAssigned.mockResolvedValue([]);
  render(<PracticeSetsHub />);
  const joined = await screen.findByTestId("joined-sets-empty");
  expect(within(joined).getByRole("link", { name: "Find practice sets in the market" }).getAttribute("href")).toBe(
    "/market?kind=practice_set",
  );
});

it("leads an empty Created tab to making the first course", async () => {
  tasks.listMyCourses.mockResolvedValue([]);
  tasks.listMyTasks.mockResolvedValue([]);
  render(<CourseAuthorView isAdmin={false} />);
  const empty = await screen.findByTestId("created-courses-empty");
  fireEvent.click(within(empty).getByRole("button", { name: "Create your first course" }));
  expect(document.activeElement).toBe(screen.getByPlaceholderText("New course title"));
});
