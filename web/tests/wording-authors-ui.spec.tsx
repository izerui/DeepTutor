import { render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

// Spec 2026-11-06: "Courses" with lessons inside them, author pages, featured rows.
const { catalog, tasks, auth, search, t } = vi.hoisted(() => ({
  catalog: { getTaxonomy: vi.fn(), searchMarket: vi.fn(), getFeatured: vi.fn(), getAuthor: vi.fn() },
  tasks: {
    listAssignedTasks: vi.fn(),
    listAssignedCourses: vi.fn(),
    getAssignedTask: vi.fn(),
    listMyCourses: vi.fn(),
    listMyTasks: vi.fn(),
    getTask: vi.fn(),
    getCourse: vi.fn(),
    getHome: vi.fn(),
  },
  auth: { isAdmin: false, enabled: true, loading: false },
  search: { value: "" },
  t: (key: string, values?: Record<string, unknown>) =>
    key.replace(/\{\{(\w+)\}\}/g, (_: string, name: string) => String(values?.[name] ?? "")),
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t, i18n: { language: "en" } }) }));
vi.mock("@/hooks/useAuthStatus", () => ({ useAuthStatus: () => auth }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/market",
  useSearchParams: () => new URLSearchParams(search.value),
}));
vi.mock("@/lib/catalog-api", async () => ({
  ...(await vi.importActual<typeof import("@/lib/catalog-api")>("@/lib/catalog-api")),
  ...catalog,
}));
vi.mock("@/lib/tasks-api", async () => ({
  ...(await vi.importActual<typeof import("@/lib/tasks-api")>("@/lib/tasks-api")),
  ...tasks,
}));
vi.mock("@/lib/practice-sets-api", () => ({ listMySets: vi.fn(() => Promise.resolve([])) }));
vi.mock("@/lib/teaching-api", () => ({ listTeacherTemplates: vi.fn(() => Promise.resolve([])) }));
vi.mock("@/lib/book-api", () => ({ bookApi: { list: vi.fn(() => Promise.resolve({ books: [] })), getSpine: vi.fn() } }));
vi.mock("@/lib/workbench-api", () => ({
  getWorkbench: vi.fn(() => Promise.resolve({ sent: [] })), getMyAiUsage: vi.fn(() => Promise.resolve(null)),
}));
vi.mock("@/components/catalog/MyListings", () => ({ MyListings: () => null }));
vi.mock("@/components/catalog/Announcements", () => ({ AnnouncementsPanel: () => null }));
vi.mock("@/components/home/AiUsage", () => ({ AiUsageNote: () => null }));

import AuthorProfile from "@/components/catalog/AuthorProfile";
import { ItemCard } from "@/components/catalog/ItemCard";
import MarketBrowser from "@/components/catalog/MarketBrowser";
import { resetTaxonomyCache } from "@/components/catalog/useTaxonomy";
import LearnerHome from "@/components/home/LearnerHome";
import { NAV_BY_HREF } from "@/components/sidebar/nav-entries";
import TasksHub from "@/components/tasks/TasksHub";
import type { AuthorPage, CatalogEntry } from "@/lib/catalog-api";
import type { AssignedTask, TaskDetail } from "@/lib/tasks-api";
import en from "../locales/en/app.json";
import zh from "../locales/zh/app.json";

const entry = (over: Partial<CatalogEntry> = {}): CatalogEntry => ({
  kind: "practice_set", id: "set_1", owner_id: "u_t", status: "listed", title: "Waves", summary: "",
  stages: [], subjects: [], difficulty: "", tags: [], approved_once: true, migrated: false, reason: "",
  submitted_at: null, listed_at: 1, updated_at: 1, needs_classification: false, learners: 4,
  author: "tina", author_role: "learner", rating: null, rating_count: 0, ...over,
});

const assigned = (over: Partial<AssignedTask> = {}): AssignedTask => ({
  key: "t_1", task_id: "t_1", kind: "note", title: "Read chapter 1", description: "", target_id: "", target_title: "",
  settings: {}, state: "open", completed_at: null, provenance: { kind: "shared", name: "tina" }, ...over,
});

const lesson = (over: Partial<TaskDetail> = {}): TaskDetail => ({
  id: "t_9", kind: "note", title: "Lab report", description: "", target_id: "", target_title: "", settings: {},
  status: "draft", created_at: 1, updated_at: 1, published_at: null, course_id: "c_1", course_title: "Optics",
  students: [], ...over,
} as TaskDetail);

beforeEach(() => {
  for (const group of [catalog, tasks]) for (const mock of Object.values(group)) mock.mockReset();
  resetTaxonomyCache();
  search.value = "";
  Object.assign(auth, { isAdmin: false, loading: false });
  catalog.getTaxonomy.mockResolvedValue({ stages: [], difficulties: [] });
  catalog.searchMarket.mockResolvedValue({
    total: 1, page: 1, page_size: 24, items: [entry({ id: "set_list", title: "Listed one" })],
    facets: { stages: {}, subjects: {}, kinds: {}, difficulties: {}, tags: [], authors: [] },
  });
  catalog.getFeatured.mockResolvedValue({ popular: [], top_rated: [] });
  tasks.listAssignedTasks.mockResolvedValue([]);
  tasks.listAssignedCourses.mockResolvedValue([]);
  tasks.listMyCourses.mockResolvedValue([]);
  tasks.listMyTasks.mockResolvedValue([]);
});

it("names the sidebar entry My courses and keeps its route", () => {
  const entryOf = NAV_BY_HREF.get("/tasks");
  expect(entryOf?.label).toBe("My courses");
  expect(entryOf?.tooltipKey).toBe("Courses tooltip");
  expect(NAV_BY_HREF.get("/kanban")?.label).toBe("Task Board");
});

it("shows only the two course tabs and lists joined lessons inside their course", async () => {
  tasks.listAssignedTasks.mockResolvedValue([assigned()]);
  tasks.listAssignedCourses.mockResolvedValue([
    { id: "c_1", title: "Optics", description: "", provenance: { kind: "shared", name: "tina" }, published_at: 1, tasks: [assigned()] },
  ]);
  render(<TasksHub />);
  expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Courses tab: learning", "Courses tab: created"]);
  // Spec 2026-11-06 (scheme 3): the tabs read Learning / Created (zh 在学 / 创作).
  expect([en["Courses tab: learning"], en["Courses tab: created"]]).toEqual(["Learning", "Created"]);
  expect([zh["Courses tab: learning"], zh["Courses tab: created"]]).toEqual(["在学", "创作"]);
  expect(screen.getByRole("tab", { name: "Courses tab: learning" }).getAttribute("aria-selected")).toBe("true");
  expect(await screen.findByText("Optics")).toBeTruthy();
  // The lesson is in its course, not repeated in a flat list.
  await waitFor(() => expect(screen.queryByText("Other lessons")).toBeNull());
  expect(screen.queryByText("Read chapter 1")).toBeNull();
});

it("opens ?task= of a joined lesson with its course open", async () => {
  search.value = "tab=joined&task=t_1";
  tasks.getAssignedTask.mockResolvedValue(assigned());
  tasks.listAssignedTasks.mockResolvedValue([assigned()]);
  tasks.listAssignedCourses.mockResolvedValue([
    { id: "c_1", title: "Optics", description: "", provenance: { kind: "shared", name: "tina" }, published_at: 1, tasks: [assigned()] },
  ]);
  render(<TasksHub />);
  expect(await screen.findByTestId("student-task-detail")).toBeTruthy();
  // The course holding it is shown open, the lesson listed in it.
  const courses = await screen.findByTestId("student-courses");
  await waitFor(() => expect(within(courses).getByText("Read chapter 1")).toBeTruthy());
});

it("opens ?task= of one's own lesson beside its course", async () => {
  search.value = "task=t_9";
  tasks.listMyCourses.mockResolvedValue([{ id: "c_1", title: "Optics", status: "draft", task_count: 1, updated_at: 1 }]);
  tasks.getTask.mockResolvedValue(lesson());
  render(<TasksHub />);
  expect(screen.getByRole("tab", { name: "Courses tab: created" }).getAttribute("aria-selected")).toBe("true");
  const pane = await screen.findByTestId("lesson-in-course");
  expect(await within(pane).findByDisplayValue("Lab report")).toBeTruthy();
  expect(await within(pane).findByRole("button", { name: "Back to the course" })).toBeTruthy();
});

it("looks for ?task= among what one joined when it is not one's own", async () => {
  search.value = "task=t_1";
  tasks.getTask.mockRejectedValue(new Error("Lesson not found."));
  tasks.getAssignedTask.mockResolvedValue(assigned());
  render(<TasksHub />);
  expect(await screen.findByTestId("student-task-detail")).toBeTruthy();
  expect(screen.getByRole("tab", { name: "Courses tab: learning" }).getAttribute("aria-selected")).toBe("true");
});

it("shows how many items an author has, their average rating and learners", async () => {
  search.value = "id=u_t";
  const page: AuthorPage = {
    id: "u_t", name: "tina", role: "learner", learners: 12, item_count: 2, rating: 4.25, rating_count: 4,
    items: [entry({ id: "a", rating: 5, rating_count: 3 }), entry({ id: "b", title: "Optics", rating: 2, rating_count: 1 })],
  };
  catalog.getAuthor.mockResolvedValue(page);
  render(<AuthorProfile />);
  const stats = await screen.findByTestId("author-stats");
  expect(within(stats).getByText("Items in the market").nextElementSibling?.textContent).toBe("2");
  expect(within(stats).getByText("4.3 (4 reviews)")).toBeTruthy();
  expect(within(stats).getByText("Learners").nextElementSibling?.textContent).toBe("12");
  expect(screen.getAllByTestId("card-rating")).toHaveLength(2);
});

it("says an author is not rated yet", async () => {
  search.value = "id=u_t";
  catalog.getAuthor.mockResolvedValue({ id: "u_t", name: "tina", role: "learner", learners: 0, item_count: 1, rating: null, rating_count: 0, items: [entry()] });
  render(<AuthorProfile />);
  expect(await screen.findByText("Not rated yet")).toBeTruthy();
});

it("links the author's name on a market card to their page", () => {
  render(<ItemCard entry={entry()} nameOf={(id) => id} />);
  expect(screen.getByRole("link", { name: "tina" }).getAttribute("href")).toBe("/market/author?id=u_t");
  expect(screen.getByRole("link", { name: "Waves" }).getAttribute("href")).toBe("/market/item?kind=practice_set&id=set_1");
});

it("shows the featured rows on the plain market and hides an empty row", async () => {
  catalog.getFeatured.mockResolvedValue({ popular: [entry({ id: "p1", title: "Crowd favourite" })], top_rated: [] });
  render(<MarketBrowser />);
  const popular = await screen.findByTestId("featured-popular");
  expect(within(popular).getByRole("heading", { name: "Most popular" })).toBeTruthy();
  expect(within(popular).getByText("Crowd favourite")).toBeTruthy();
  expect(screen.queryByTestId("featured-top-rated")).toBeNull();
});

it("hides the featured rows while searching", async () => {
  search.value = "q=wave";
  catalog.getFeatured.mockResolvedValue({ popular: [entry({ id: "p1", title: "Crowd favourite" })], top_rated: [entry({ id: "r1" })] });
  render(<MarketBrowser />);
  expect(await screen.findByText("Listed one")).toBeTruthy();
  expect(screen.queryByTestId("market-featured")).toBeNull();
});

it("shows both featured rows when both have items", async () => {
  catalog.getFeatured.mockResolvedValue({
    popular: [entry({ id: "p1", title: "Crowd favourite" })],
    top_rated: [entry({ id: "r1", title: "Well liked", rating: 4.8, rating_count: 5 })],
  });
  render(<MarketBrowser />);
  const rated = await screen.findByTestId("featured-top-rated");
  expect(within(rated).getByRole("heading", { name: "Highly rated" })).toBeTruthy();
  expect(within(rated).getByText("Well liked")).toBeTruthy();
});

it("puts Most popular beside New in the market on the learner home", async () => {
  tasks.getHome.mockResolvedValue({
    assigned: [], history_count: 0, mine: [], market: [entry({ id: "n1", title: "Fresh" })],
  });
  catalog.getFeatured.mockResolvedValue({ popular: [entry({ id: "p1", title: "Crowd favourite" })], top_rated: [] });
  render(<LearnerHome />);
  expect(within(await screen.findByTestId("home-market")).getByText("Fresh")).toBeTruthy();
  const popular = await screen.findByTestId("home-popular");
  expect(within(popular).getByRole("heading", { name: "Most popular" })).toBeTruthy();
  expect(within(popular).getByRole("link", { name: /Crowd favourite/ }).getAttribute("href")).toBe("/market/item?kind=practice_set&id=p1");
});
