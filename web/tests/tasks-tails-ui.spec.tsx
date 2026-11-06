import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

// Tails after the course renaming: lessons in no course on "Created", no inner "Courses" heading on "Learning".
const { tasks, auth, search, t } = vi.hoisted(() => ({
  tasks: {
    listAssignedTasks: vi.fn(),
    listAssignedCourses: vi.fn(),
    getAssignedTask: vi.fn(),
    listMyCourses: vi.fn(),
    listMyTasks: vi.fn(),
    getTask: vi.fn(),
    getCourse: vi.fn(),
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
  usePathname: () => "/tasks",
  useSearchParams: () => new URLSearchParams(search.value),
}));
vi.mock("@/lib/tasks-api", async () => ({
  ...(await vi.importActual<typeof import("@/lib/tasks-api")>("@/lib/tasks-api")),
  ...tasks,
}));
vi.mock("@/lib/practice-sets-api", () => ({ listMySets: vi.fn(() => Promise.resolve([])) }));
vi.mock("@/lib/teaching-api", () => ({ listAuthorTemplates: vi.fn(() => Promise.resolve([])) }));
vi.mock("@/lib/book-api", () => ({ bookApi: { list: vi.fn(() => Promise.resolve({ books: [] })), getSpine: vi.fn() } }));
vi.mock("@/components/catalog/Announcements", () => ({ AnnouncementsPanel: () => null }));
vi.mock("@/components/catalog/ListingPanel", () => ({ ListingPanel: () => null }));

import TasksHub from "@/components/tasks/TasksHub";
import type { AssignedTask, MyTask, TaskDetail } from "@/lib/tasks-api";
import de from "../locales/de/app.json";
import en from "../locales/en/app.json";
import fr from "../locales/fr/app.json";
import pl from "../locales/pl/app.json";
import uk from "../locales/uk/app.json";
import zh from "../locales/zh/app.json";

const mine = (over: Partial<MyTask> = {}): MyTask => ({
  id: "t_loose", kind: "note", title: "Stray note", description: "", target_id: "", target_title: "", settings: {},
  status: "draft", created_at: 1, updated_at: 1, published_at: null, course_id: "", course_title: "",
  total: 0, completed: 0, target_available: true, ...over,
});

const assigned = (over: Partial<AssignedTask> = {}): AssignedTask => ({
  key: "t_1", task_id: "t_1", kind: "note", title: "Read chapter 1", description: "", target_id: "", target_title: "",
  settings: {}, state: "open", completed_at: null, provenance: { kind: "shared", name: "tina" }, ...over,
});

beforeEach(() => {
  for (const mock of Object.values(tasks)) mock.mockReset();
  search.value = "tab=courses";
  Object.assign(auth, { isAdmin: false, loading: false });
  tasks.listAssignedTasks.mockResolvedValue([]);
  tasks.listAssignedCourses.mockResolvedValue([]);
  tasks.listMyCourses.mockResolvedValue([{ id: "c_1", title: "Optics", status: "draft", task_count: 1, updated_at: 1 }]);
  tasks.listMyTasks.mockResolvedValue([]);
});

it("lists one's lessons in no course under the courses and opens one in the lesson editor", async () => {
  tasks.listMyTasks.mockResolvedValue([mine(), mine({ id: "t_in", title: "Inside", course_id: "c_1", course_title: "Optics" })]);
  tasks.getTask.mockResolvedValue({ ...mine(), students: [] } as TaskDetail);
  render(<TasksHub />);
  const section = await screen.findByRole("region", { name: "Lessons in no course" });
  expect(within(section).getByText("Add them to a course from the course editor, or open one to delete or withdraw it.")).toBeTruthy();
  expect(within(section).getByText("Stray note")).toBeTruthy();
  expect(within(section).queryByText("Inside")).toBeNull();
  fireEvent.click(within(section).getByRole("button", { name: /Stray note/ }));
  const pane = await screen.findByTestId("lesson-in-course");
  expect(await within(pane).findByDisplayValue("Stray note")).toBeTruthy();
  expect(within(pane).getByRole("button", { name: "Delete" })).toBeTruthy();
  expect(tasks.getTask).toHaveBeenCalledWith("t_loose");
});

it("hides the lessons-in-no-course section when every lesson is in a course", async () => {
  tasks.listMyTasks.mockResolvedValue([mine({ id: "t_in", title: "Inside", course_id: "c_1", course_title: "Optics" })]);
  render(<TasksHub />);
  expect(await screen.findByText("Optics")).toBeTruthy();
  await waitFor(() => expect(tasks.listMyTasks).toHaveBeenCalled());
  expect(screen.queryByText("Lessons in no course")).toBeNull();
  expect(screen.queryByTestId("lessons-in-no-course")).toBeNull();
});

it("translates the new strings in every locale", () => {
  for (const locale of [en, zh, de, fr, pl, uk] as Record<string, string>[]) {
    expect(locale["Lessons in no course"]).toBeTruthy();
    expect(locale["Add them to a course from the course editor, or open one to delete or withdraw it."]).toBeTruthy();
  }
  expect((zh as Record<string, string>)["Lessons in no course"]).toBe("不在任何课程里的课时");
});

it("does not repeat a Courses heading under the page title on either tab", async () => {
  search.value = "tab=joined";
  tasks.listAssignedTasks.mockResolvedValue([assigned()]);
  tasks.listAssignedCourses.mockResolvedValue([
    { id: "c_9", title: "Waves", description: "", provenance: { kind: "shared", name: "tina" }, published_at: 1, tasks: [assigned()] },
  ]);
  render(<TasksHub />);
  expect(await screen.findByText("Waves")).toBeTruthy();
  expect(screen.getByRole("heading", { level: 1, name: "My courses" })).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "Courses" })).toBeNull();
  // Still a named region for assistive technology.
  expect(screen.getByRole("region", { name: "Courses" })).toBeTruthy();
  fireEvent.click(screen.getByRole("tab", { name: "Courses tab: created" }));
  expect(await screen.findByText("Optics")).toBeTruthy();
  expect(screen.queryByRole("heading", { name: "Courses" })).toBeNull();
});
