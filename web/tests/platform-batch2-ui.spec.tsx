import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

// Platform batch 2: classes retired, tasks live in courses, classification and tags.
const { catalog, tasks, auth, authStatus, search, t } = vi.hoisted(() => ({
  catalog: {
    getTaxonomy: vi.fn(),
    getAdminTaxonomy: vi.fn(),
    updateTaxonomyNode: vi.fn(),
    addTaxonomyNode: vi.fn(),
    moveTaxonomyNode: vi.fn(),
    deleteTaxonomyNode: vi.fn(),
    getAdminTags: vi.fn(),
    mergeTags: vi.fn(),
    unmergeTag: vi.fn(),
    getReviewItem: vi.fn(),
    classifyListing: vi.fn(),
    listReviewQueue: vi.fn(),
    listMyListings: vi.fn(),
  },
  tasks: {
    listMyTasks: vi.fn(),
    getTask: vi.fn(),
    publishTask: vi.fn(),
    updateTask: vi.fn(),
    listAssignedTasks: vi.fn(),
    listAssignedCourses: vi.fn(),
    listMyCourses: vi.fn(),
    getHome: vi.fn(),
  },
  auth: {
    register: vi.fn(),
    checkIsFirstUser: vi.fn(),
    fetchAuthStatus: vi.fn(),
    fetchRegistrationOpen: vi.fn(),
  },
  authStatus: { isAdmin: false, loading: false },
  search: { value: "" },
  t: (key: string, values?: Record<string, unknown>) =>
    key.replace(/\{\{(\w+)\}\}/g, (_: string, name: string) => String(values?.[name] ?? "")),
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t, i18n: { language: "en" } }) }));
vi.mock("@/hooks/useAuthStatus", () => ({ useAuthStatus: () => authStatus }));
const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn() }),
  usePathname: () => "/admin/catalog",
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
vi.mock("@/lib/auth", () => auth);
vi.mock("@/lib/practice-sets-api", () => ({ listMySets: vi.fn(() => Promise.resolve([])) }));
vi.mock("@/lib/teaching-api", () => ({ listTeacherTemplates: vi.fn(() => Promise.resolve([])) }));
vi.mock("@/lib/workbench-api", () => ({
  getSchoolAiLimit: vi.fn(() => Promise.resolve(0)), setSchoolAiLimit: vi.fn(), getMyAiUsage: vi.fn(() => Promise.resolve(null)),
  AI_LIMIT_PRESETS: [{ tokens: 0, label: "No limit" }],
}));
vi.mock("@/lib/book-api", () => ({ bookApi: { list: vi.fn(() => Promise.resolve({ books: [] })), getSpine: vi.fn() } }));

import RegisterPage from "@/app/(auth)/register/page";
import { AdminNav } from "@/components/catalog/AdminNav";
import { TagAdmin, TaxonomyAdmin } from "@/components/catalog/CatalogAdmin";
import { ReviewDetail } from "@/components/catalog/ReviewDesk";
import { TaxonomyPicker } from "@/components/catalog/TaxonomyPicker";
import { resetTaxonomyCache } from "@/components/catalog/useTaxonomy";
import { AdminHome } from "@/components/home/Workbench";
import TasksHub from "@/components/tasks/TasksHub";
import type { AdminTaxonomy, CatalogEntry, Taxonomy } from "@/lib/catalog-api";
import type { MyTask, TaskDetail } from "@/lib/tasks-api";

const names = (en: string, zh = en) => ({ zh, en });
const publicTaxonomy: Taxonomy = {
  stages: [{ id: "senior", names: names("Senior high"), subjects: [{ id: "senior.math", names: names("Math"), children: [] }] }],
  difficulties: [{ id: "basic", names: names("Basic") }],
};
const adminTree = (over: Partial<{ hidden: boolean; custom: boolean; in_use: number; en: string }> = {}): AdminTaxonomy => ({
  stages: [
    {
      id: "senior", names: names("Senior high"), hidden: false, custom: false, in_use: 4,
      subjects: [
        {
          id: "senior.math", names: names(over.en ?? "Math"), hidden: over.hidden ?? false, custom: over.custom ?? false,
          in_use: over.in_use ?? 2, children: [],
        },
      ],
    },
  ],
  difficulties: [{ id: "basic", names: names("Basic") }],
});

beforeEach(() => {
  [catalog, tasks, auth].forEach((group) => Object.values(group).forEach((fn) => fn.mockReset()));
  replace.mockReset();
  search.value = "";
  Object.assign(authStatus, { isAdmin: false, loading: false });
  resetTaxonomyCache();
  catalog.getTaxonomy.mockResolvedValue(publicTaxonomy);
  catalog.listReviewQueue.mockResolvedValue([]);
  tasks.listAssignedTasks.mockResolvedValue([]);
  tasks.listAssignedCourses.mockResolvedValue([]);
  tasks.listMyCourses.mockResolvedValue([]);
  tasks.getHome.mockResolvedValue({ mine: [], assigned: [], history_count: 0 });
  catalog.listMyListings.mockResolvedValue([]);
});



it("registers without an invite and never lands on the classes", async () => {
  search.value = "invite=tok_1";
  auth.fetchAuthStatus.mockResolvedValue({ authenticated: false });
  auth.checkIsFirstUser.mockResolvedValue(false);
  auth.fetchRegistrationOpen.mockResolvedValue(true);
  auth.register.mockResolvedValue({ ok: true });
  render(<RegisterPage />);
  fireEvent.change(await screen.findByLabelText("Email or username"), { target: { value: "sam" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "password1" } });
  fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: "password1" } });
  fireEvent.click(screen.getByRole("button", { name: "Create account" }));
  await waitFor(() => expect(auth.register).toHaveBeenCalledWith("sam", "password1"));
  expect(replace).toHaveBeenCalledWith("/login?registered=1");
});

it("tells a visitor to ask an administrator while registration is closed, invite or not", async () => {
  search.value = "invite=tok_1";
  auth.fetchAuthStatus.mockResolvedValue({ authenticated: false });
  auth.checkIsFirstUser.mockResolvedValue(false);
  auth.fetchRegistrationOpen.mockResolvedValue(false);
  render(<RegisterPage />);
  expect(await screen.findByText("Registration is closed. Ask an administrator for an account.")).toBeTruthy();
  expect(screen.queryByLabelText("Email or username")).toBeNull();
});

const task = (over: Partial<MyTask>): MyTask => ({
  id: "t", kind: "note", title: "T", description: "", target_id: "", target_title: "", settings: {},
  status: "published", created_at: 1, updated_at: 1, published_at: 1,
  course_id: "", course_title: "", total: 0, completed: 0, target_available: true, ...over,
});

it("shows courses only: what one joined and what one made, with no separate list of lessons", async () => {
  tasks.listMyTasks.mockResolvedValue([task({ id: "t1", title: "Essay", course_id: "c1", course_title: "Waves" })]);
  tasks.listMyCourses.mockResolvedValue([{ id: "c1", title: "Waves", status: "draft", task_count: 1, updated_at: 1 }]);
  // Spec 2026-11-06: the older ``?tab=tasks`` lands on one's own courses.
  search.value = "tab=tasks";
  render(<TasksHub />);
  expect(screen.getByRole("heading", { name: "Courses" })).toBeTruthy();
  expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Courses I joined", "Courses I made"]);
  expect(screen.getByRole("tab", { name: "Courses I made" }).getAttribute("aria-selected")).toBe("true");
  expect(await screen.findByText("Waves")).toBeTruthy();
  expect(screen.getByText("Draft · 1 lessons")).toBeTruthy();
  expect(screen.queryByText("Essay")).toBeNull();
  expect(screen.queryByRole("tab", { name: "Tasks" })).toBeNull();
});

const detail = (over: Partial<TaskDetail>): TaskDetail => ({
  ...task({}), id: "t9", title: "Draft", status: "draft", published_at: null, students: [], ...over,
} as TaskDetail);

it("offers Publish only for a draft that sits in a course", async () => {
  search.value = "task=t9";
  tasks.listMyTasks.mockResolvedValue([task({ id: "t9", title: "Draft", status: "draft" })]);
  tasks.getTask.mockResolvedValue(detail({ kind: "note" }));
  const { unmount } = render(<TasksHub />);
  expect(await screen.findByText("This lesson is in no course. Add it to a course; it is published with the course.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Publish" })).toBeNull();
  expect(screen.getByRole("button", { name: "Delete" })).toBeTruthy();
  unmount();

  tasks.getTask.mockResolvedValue(detail({ kind: "note", course_id: "c1", course_title: "Waves" }));
  tasks.publishTask.mockResolvedValue({});
  render(<TasksHub />);
  fireEvent.click(await screen.findByRole("button", { name: "Publish" }));
  await waitFor(() => expect(tasks.publishTask).toHaveBeenCalledWith("t9"));
});

it("renames, hides, adds, moves and deletes classification entries and refreshes the pickers", async () => {
  catalog.getAdminTaxonomy.mockResolvedValue(adminTree({ custom: true, in_use: 0 }));
  catalog.updateTaxonomyNode.mockResolvedValue(adminTree({ custom: true, in_use: 0, en: "Maths" }));
  render(<TaxonomyAdmin />);
  const row = await screen.findByTestId("node-senior.math");
  expect(within(row).getByText("In use: 0")).toBeTruthy();
  fireEvent.change(within(row).getByLabelText("English name of Math"), { target: { value: "Maths" } });
  fireEvent.click(within(row).getByRole("button", { name: "Save" }));
  await waitFor(() => expect(catalog.updateTaxonomyNode).toHaveBeenCalledWith("senior.math", { name_zh: "Math", name_en: "Maths" }));

  catalog.updateTaxonomyNode.mockResolvedValue(adminTree({ custom: true, in_use: 0, en: "Maths", hidden: true }));
  fireEvent.click(await screen.findByLabelText("Hide Maths"));
  await waitFor(() => expect(catalog.updateTaxonomyNode).toHaveBeenLastCalledWith("senior.math", { hidden: true }));

  catalog.addTaxonomyNode.mockResolvedValue(adminTree({ custom: true, in_use: 0, en: "Maths", hidden: true }));
  fireEvent.click(screen.getByRole("button", { name: "+ Add subject" }));
  const form = screen.getByRole("form", { name: "Add subject" });
  fireEvent.change(within(form).getByLabelText("Chinese name"), { target: { value: "物理" } });
  fireEvent.change(within(form).getByLabelText("English name"), { target: { value: "Physics" } });
  fireEvent.click(within(form).getByRole("button", { name: "Add" }));
  await waitFor(() => expect(catalog.addTaxonomyNode).toHaveBeenCalledWith("senior", "物理", "Physics"));

  // The stage is built in: no delete, only hide.
  expect(within(screen.getByTestId("node-senior")).queryByRole("button", { name: /Delete/ })).toBeNull();
  catalog.deleteTaxonomyNode.mockRejectedValue(new Error("Content uses this entry; hide it instead."));
  vi.spyOn(window, "confirm").mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", { name: "Delete Maths" }));
  expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Content uses this entry; hide it instead.");
  expect(catalog.deleteTaxonomyNode).toHaveBeenCalledWith("senior.math");

  // Any change makes the market's classification load again.
  catalog.getTaxonomy.mockClear();
  render(<TaxonomyPicker taxonomy={publicTaxonomy} value={{ stages: [], subjects: [], grades: [], difficulty: "" }} onChange={vi.fn()} />);
  const { loadTaxonomy } = await import("@/components/catalog/useTaxonomy");
  await loadTaxonomy();
  expect(catalog.getTaxonomy).toHaveBeenCalledTimes(1);
});

it("edits the names in the other languages of an entry and of a new one", async () => {
  const tree = adminTree({ custom: true, in_use: 0 });
  tree.stages[0].subjects[0].names = { ...tree.stages[0].subjects[0].names, de: "Mathematik", fr: "Maths" };
  catalog.getAdminTaxonomy.mockResolvedValue(tree);
  catalog.updateTaxonomyNode.mockResolvedValue(tree);
  render(<TaxonomyAdmin />);
  const row = await screen.findByTestId("node-senior.math");
  expect(within(row).queryByLabelText("Deutsch name of Math")).toBeNull();
  fireEvent.click(within(row).getByRole("button", { name: "Other languages of Math" }));
  expect((within(row).getByLabelText("Deutsch name of Math") as HTMLInputElement).value).toBe("Mathematik");
  expect((within(row).getByLabelText("Français name of Math") as HTMLInputElement).value).toBe("Maths");
  fireEvent.change(within(row).getByLabelText("Polski name of Math"), { target: { value: "Matematyka" } });
  fireEvent.change(within(row).getByLabelText("Français name of Math"), { target: { value: "" } });
  fireEvent.click(within(row).getByRole("button", { name: "Save" }));
  await waitFor(() => expect(catalog.updateTaxonomyNode).toHaveBeenCalledWith("senior.math", {
    name_zh: "Math", name_en: "Math", names: { de: "Mathematik", fr: "", pl: "Matematyka", uk: "" },
  }));

  catalog.addTaxonomyNode.mockResolvedValue(tree);
  fireEvent.click(screen.getByRole("button", { name: "+ Add subject" }));
  const form = screen.getByRole("form", { name: "Add subject" });
  fireEvent.change(within(form).getByLabelText("English name"), { target: { value: "Physics" } });
  fireEvent.click(within(form).getByRole("button", { name: "Other languages" }));
  fireEvent.change(within(form).getByLabelText("Українська name"), { target: { value: "Фізика" } });
  fireEvent.click(within(form).getByRole("button", { name: "Add" }));
  await waitFor(() => expect(catalog.addTaxonomyNode).toHaveBeenCalledWith("senior", "", "Physics", { de: "", fr: "", pl: "", uk: "Фізика" }));
});

it("merges one tag into another and undoes a merge", async () => {
  catalog.getAdminTags.mockResolvedValue({ tags: [{ tag: "algebra", count: 3 }, { tag: "Algebra I", count: 1 }], merges: [] });
  catalog.mergeTags.mockResolvedValue({ tags: [{ tag: "algebra", count: 4 }], merges: [{ from: "Algebra I", to: "algebra", at: 1 }] });
  render(<TagAdmin />);
  expect(await screen.findByText("No merges yet.")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Tag to merge"), { target: { value: "Algebra I" } });
  fireEvent.change(screen.getByLabelText("Tag to keep"), { target: { value: "algebra" } });
  fireEvent.click(screen.getByRole("button", { name: "Merge" }));
  await waitFor(() => expect(catalog.mergeTags).toHaveBeenCalledWith("Algebra I", "algebra"));
  expect(await screen.findByText("#Algebra I → #algebra")).toBeTruthy();
  expect(screen.getByText("Undoing stops future replacement; tags already changed stay.")).toBeTruthy();

  catalog.unmergeTag.mockResolvedValue({ tags: [{ tag: "algebra", count: 4 }], merges: [] });
  fireEvent.click(screen.getByRole("button", { name: "Undo merging Algebra I" }));
  await waitFor(() => expect(catalog.unmergeTag).toHaveBeenCalledWith("Algebra I"));
  expect(await screen.findByText("No merges yet.")).toBeTruthy();
});

const listed: CatalogEntry = {
  kind: "practice_set", id: "set_1", owner_id: "u_t", status: "listed", title: "Week 3", summary: "Practice.",
  stages: ["senior"], subjects: ["senior.math"], difficulty: "basic", tags: ["algebra"], approved_once: true, migrated: false,
  reason: "", submitted_at: 1, listed_at: 1, updated_at: 1, needs_classification: false, learners: 2, author: "tina", author_role: "teacher",
};

it("lets an administrator edit the classification of a listing in the market, not its title", async () => {
  catalog.getReviewItem.mockResolvedValue({ ...listed, preview: {}, events: [] });
  catalog.classifyListing.mockResolvedValue({ ...listed, difficulty: "", tags: ["algebra", "exam"] });
  render(<ReviewDetail entry={listed} onDone={vi.fn()} canClassify />);
  fireEvent.click(await screen.findByRole("button", { name: "Edit classification" }));
  const editor = await screen.findByTestId("classification-editor");
  expect(within(editor).queryByLabelText("Title")).toBeNull();
  fireEvent.change(within(editor).getByRole("combobox"), { target: { value: "" } });
  const tagBox = within(editor).getByLabelText("Add a tag");
  fireEvent.change(tagBox, { target: { value: "exam" } });
  fireEvent.keyDown(tagBox, { key: "Enter" });
  fireEvent.click(within(editor).getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(catalog.classifyListing).toHaveBeenCalledWith("practice_set", "set_1", {
      stages: ["senior"], subjects: ["senior.math"], grades: [], difficulty: "", tags: ["algebra", "exam"],
    }),
  );
  await waitFor(() => expect(screen.queryByTestId("classification-editor")).toBeNull());
});

it("offers no classification editing in the review queue", async () => {
  catalog.getReviewItem.mockResolvedValue({ ...listed, status: "pending", preview: {}, events: [] });
  render(<ReviewDetail entry={{ ...listed, status: "pending" }} onDone={vi.fn()} />);
  expect(await screen.findByRole("button", { name: "Approve" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Edit classification" })).toBeNull();
});

it("keeps a hidden entry a listing already uses, named where known", async () => {
  catalog.getAdminTaxonomy.mockResolvedValue(adminTree({ hidden: true }));
  const change = vi.fn();
  const visible: Taxonomy = { ...publicTaxonomy, stages: [{ ...publicTaxonomy.stages[0], subjects: [] }] };
  render(<TaxonomyPicker taxonomy={visible} value={{ stages: ["senior"], subjects: ["senior.math"], grades: [], difficulty: "" }} onChange={change} />);
  const kept = screen.getByTestId("taxonomy-kept");
  expect(await within(kept).findByRole("button", { name: "Math" })).toBeTruthy();
  fireEvent.click(within(kept).getByRole("button", { name: "Math" }));
  expect(change).toHaveBeenCalledWith({ stages: ["senior"], subjects: [], grades: [], difficulty: "" });
});

it("shows the raw id of a hidden entry when its name cannot be looked up", async () => {
  catalog.getAdminTaxonomy.mockRejectedValue(new Error("Only administrators manage the classification."));
  render(<TaxonomyPicker taxonomy={publicTaxonomy} value={{ stages: ["senior"], subjects: ["senior.old"], grades: [], difficulty: "" }} onChange={vi.fn()} />);
  expect(await within(screen.getByTestId("taxonomy-kept")).findByRole("button", { name: "senior.old" })).toBeTruthy();
});

it("puts classification and tags in the administration and on the admin home", async () => {
  const nav = render(<AdminNav />);
  expect(within(nav.container).getByRole("link", { name: "Classification and tags" }).getAttribute("href")).toBe("/admin/catalog");
  await waitFor(() => expect(catalog.listReviewQueue).toHaveBeenCalled());
  nav.unmount();
  render(<AdminHome />);
  const home = screen.getByTestId("admin-home");
  await waitFor(() => expect(catalog.listMyListings).toHaveBeenCalled());
  expect(within(home).getByRole("link", { name: "Classification and tags" }).getAttribute("href")).toBe("/admin/catalog");
  expect(within(home).queryByText("Classes, departments and grades")).toBeNull();
});
