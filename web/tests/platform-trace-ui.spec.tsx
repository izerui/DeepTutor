import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

// Requirements trace: dead ends and leftover wording (traceability #1, #3–#7, #9, #10).
const { auth, catalog, practice, tasks, admin, comments, workbench, router, search, t } = vi.hoisted(() => ({
  auth: { value: { isAdmin: false, isTeacher: false, enabled: true, loading: false } },
  catalog: { listReviewQueue: vi.fn(), listReviewListed: vi.fn(), getReviewItem: vi.fn(), getLearnerReport: vi.fn(),
    getReportPoints: vi.fn(), getLearnerDetail: vi.fn() },
  practice: { listMySets: vi.fn(), listAssigned: vi.fn(), listWithdrawable: vi.fn(), remedialFromPath: vi.fn() },
  tasks: { getHome: vi.fn(), listMyTasks: vi.fn(), listAssignedTasks: vi.fn(), getAssignedTask: vi.fn(), listAssignedCourses: vi.fn(),
    listMyCourses: vi.fn(), getCourse: vi.fn(), handOverCourse: vi.fn(), listCalendar: vi.fn() },
  admin: { listUsers: vi.fn() },
  comments: { writtenComments: vi.fn(), writeComment: vi.fn(), deleteComment: vi.fn(), myComments: vi.fn() },
  workbench: { getWorkbench: vi.fn() },
  router: { replace: vi.fn(), push: vi.fn() },
  search: { value: "" },
  t: (key: string, values?: Record<string, unknown>) =>
    key.replace(/\{\{(\w+)\}\}/g, (_: string, name: string) => String(values?.[name] ?? "")),
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t, i18n: { language: "en" } }) }));
vi.mock("@/hooks/useAuthStatus", () => ({ useAuthStatus: () => auth.value }));
vi.mock("next/navigation", () => ({
  useRouter: () => router,
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(search.value),
}));
vi.mock("@/lib/catalog-api", async () => ({
  ...(await vi.importActual<typeof import("@/lib/catalog-api")>("@/lib/catalog-api")),
  ...catalog,
}));
vi.mock("@/lib/practice-sets-api", async () => ({
  ...(await vi.importActual<typeof import("@/lib/practice-sets-api")>("@/lib/practice-sets-api")),
  ...practice,
}));
vi.mock("@/lib/tasks-api", async () => ({
  ...(await vi.importActual<typeof import("@/lib/tasks-api")>("@/lib/tasks-api")),
  ...tasks,
}));
vi.mock("@/lib/admin-api", () => admin);
vi.mock("@/lib/comments-api", () => comments);
vi.mock("@/lib/workbench-api", () => ({
  ...workbench,
  getSchoolAiLimit: vi.fn(() => Promise.resolve(0)), setSchoolAiLimit: vi.fn(), getMyAiUsage: vi.fn(() => Promise.resolve(null)),
  AI_LIMIT_PRESETS: [{ tokens: 0, label: "No limit" }],
}));
vi.mock("@/components/practice-sets/TakeSet", () => ({ TakeSet: ({ setId }: { setId: string }) => <p>{`take ${setId}`}</p> }));
vi.mock("@/components/practice-sets/SetEditor", () => ({ SetEditor: ({ setId }: { setId: string }) => <p>{`edit ${setId}`}</p> }));
vi.mock("@/components/tasks/StudentTaskDetail", () => ({ StudentTaskDetail: ({ task }: { task: { title: string } }) => <p>{`open ${task.title}`}</p> }));
vi.mock("@/components/catalog/MyListings", () => ({ MyListings: () => null }));
vi.mock("@/components/catalog/AdminNav", () => ({ usePendingReviews: () => 0, AdminNav: () => null }));
vi.mock("@/components/catalog/ListingPanel", () => ({ ListingPanel: () => null }));
vi.mock("@/components/catalog/Announcements", () => ({ AnnouncementsPanel: () => null }));
vi.mock("@/components/catalog/CatalogPreview", () => ({ CatalogPreview: () => null }));

import ReviewDesk from "@/components/catalog/ReviewDesk";
import LearnerReportPage from "@/components/catalog/LearnerReport";
import HomeRoute from "@/components/home/HomeRoute";
import StudentHome from "@/components/home/StudentHome";
import { authorTaskHref } from "@/components/home/task-links";
import { AdminHome, TeacherWorkbench } from "@/components/home/Workbench";
import { notificationText } from "@/components/notifications/NotificationBell";
import PracticeSetsHub from "@/components/practice-sets/PracticeSetsHub";
import { navVisibleForRole } from "@/components/sidebar/nav-entries";
import { CourseAuthorView } from "@/components/tasks/Courses";
import { activeTeachers, HandOverPanel } from "@/components/tasks/HandOver";
import TaskCalendar from "@/components/tasks/TaskCalendar";
import TasksHub from "@/components/tasks/TasksHub";
import type { UserRecord } from "@/lib/admin-api";
import type { CatalogEntry } from "@/lib/catalog-api";
import type { AppNotification } from "@/lib/notifications-api";
import type { AssignedSet } from "@/lib/practice-sets-api";
import type { AssignedTask, HomeData } from "@/lib/tasks-api";

const student = { isAdmin: false, isTeacher: false, enabled: true, loading: false };
const teacher = { ...student, isTeacher: true };
const administrator = { ...student, isAdmin: true };

beforeEach(() => {
  for (const group of [catalog, practice, tasks, admin, comments, workbench]) {
    for (const fn of Object.values(group)) (fn as ReturnType<typeof vi.fn>).mockReset();
  }
  router.replace.mockReset();
  router.push.mockReset();
  search.value = "";
  auth.value = student;
  practice.listWithdrawable.mockResolvedValue([]);
  tasks.listAssignedCourses.mockResolvedValue([]);
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

const assignedSet = (over: Partial<AssignedSet> = {}): AssignedSet => ({
  id: "set_x", title: "Fractions", status: "published", current_revision: 1, due_at: null, teacher: "tina",
  submissions: 0, best_score: null, max_score: 10, ...over,
} as AssignedSet);

const home = (over: Partial<HomeData> = {}): HomeData => ({
  assigned: [], history_count: 0, mine: [], can_create_path: false, market: [], announcements: [], joined: [], ...over,
});

// ── #1 ──

it("shows students the practice sets entry and lists what they joined or were sent", async () => {
  expect(navVisibleForRole("/practice-sets", "student", {})).toBe(true);
  practice.listAssigned.mockResolvedValue([assignedSet()]);
  render(<PracticeSetsHub />);
  expect(await screen.findByText("Fractions")).toBeTruthy();
  expect(screen.getByText("Practice sets you joined or were sent. Your answers go to your own question bank.")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Start" }));
  expect(screen.getByText("take set_x")).toBeTruthy();
});

it("opens a sent set from its link for a student", async () => {
  search.value = "set=set_r";
  practice.listAssigned.mockResolvedValue([]);
  render(<PracticeSetsHub />);
  expect(await screen.findByText("take set_r")).toBeTruthy();
});

// ── #3 ──

it("opens someone else's set for a teacher in the Joined tab to answer, and their own in the editor", async () => {
  auth.value = teacher;
  search.value = "set=set_other";
  practice.listMySets.mockResolvedValue([{ id: "set_own", title: "Mine", status: "published", current_revision: 1 }]);
  practice.listAssigned.mockResolvedValue([]);
  const { unmount } = render(<PracticeSetsHub />);
  expect(await screen.findByText("take set_other")).toBeTruthy();
  expect(screen.getByRole("tab", { name: "Practice sets I joined" }).getAttribute("aria-selected")).toBe("true");
  expect(screen.queryByText("edit set_other")).toBeNull();
  unmount();

  search.value = "set=set_own";
  render(<PracticeSetsHub />);
  expect(await screen.findByText("edit set_own")).toBeTruthy();
  expect(screen.getByRole("tab", { name: "My practice sets" }).getAttribute("aria-selected")).toBe("true");
});

it("lists a teacher's joined sets under their own tab", async () => {
  auth.value = teacher;
  practice.listMySets.mockResolvedValue([]);
  practice.listAssigned.mockResolvedValue([assignedSet({ id: "set_j", title: "Joined quiz" })]);
  render(<PracticeSetsHub />);
  fireEvent.click(await screen.findByRole("tab", { name: "Practice sets I joined" }));
  expect(await screen.findByText("Joined quiz")).toBeTruthy();
});

// ── #5 ──

const user = (over: Partial<UserRecord>): UserRecord => ({ id: "u", username: "u", role: "student", created_at: "", ...over });

it("hands over only to active teachers, after confirming", async () => {
  const users = [
    user({ id: "u_zoe", username: "zoe", role: "teacher" }),
    user({ id: "u_gone", username: "gone", role: "teacher", disabled: true }),
    user({ id: "u_sam", username: "sam", role: "student" }),
    user({ id: "u_amy", username: "amy", role: "teacher" }),
  ];
  expect(activeTeachers(users).map((u) => u.username)).toEqual(["amy", "zoe"]);
  admin.listUsers.mockResolvedValue(users);
  const handOver = vi.fn(() => Promise.resolve());
  const onDone = vi.fn();
  render(<HandOverPanel handOver={handOver} onDone={onDone} />);
  const select = await screen.findByRole("combobox", { name: "Teacher" });
  expect(within(select).queryByRole("option", { name: "gone" })).toBeNull();
  expect(within(select).queryByRole("option", { name: "sam" })).toBeNull();
  const button = screen.getByRole("button", { name: "Hand over" }) as HTMLButtonElement;
  expect(button.disabled).toBe(true);
  fireEvent.change(select, { target: { value: "u_zoe" } });
  fireEvent.click(button);
  expect(window.confirm).toHaveBeenCalledWith("Hand it over to zoe? You will no longer hold it.");
  await waitFor(() => expect(onDone).toHaveBeenCalled());
  expect(handOver).toHaveBeenCalledWith("u_zoe");
});

it("offers administrators to hand a course over and shows the server's reason", async () => {
  auth.value = administrator;
  admin.listUsers.mockResolvedValue([user({ id: "u_zoe", username: "zoe", role: "teacher" })]);
  tasks.listMyCourses.mockResolvedValue([{ id: "crs_1", title: "Physics", status: "published", task_count: 0, updated_at: 1 }]);
  tasks.listMyTasks.mockResolvedValue([]);
  tasks.getCourse.mockResolvedValue({ id: "crs_1", title: "Physics", description: "", status: "published", published_at: 1, tasks: [] });
  tasks.handOverCourse.mockRejectedValueOnce(new Error("Choose an active teacher."));
  render(<CourseAuthorView isAdmin onOpenTask={() => undefined} />);
  fireEvent.click(await screen.findByRole("button", { name: /Physics/ }));
  const panel = await screen.findByTestId("hand-over");
  fireEvent.change(await within(panel).findByRole("combobox", { name: "Teacher" }), { target: { value: "u_zoe" } });
  fireEvent.click(within(panel).getByRole("button", { name: "Hand over" }));
  expect(await within(panel).findByRole("alert")).toBeTruthy();
  expect(tasks.handOverCourse).toHaveBeenCalledWith("crs_1", "u_zoe");

  tasks.handOverCourse.mockResolvedValueOnce({ course: { id: "crs_1", owner_user_id: "u_zoe" } });
  tasks.listMyCourses.mockResolvedValue([]);
  fireEvent.click(within(panel).getByRole("button", { name: "Hand over" }));
  await waitFor(() => expect(screen.queryByTestId("course-editor")).toBeNull());
});

it("offers no hand-over to teachers", async () => {
  auth.value = teacher;
  tasks.listMyCourses.mockResolvedValue([{ id: "crs_1", title: "Physics", status: "draft", task_count: 0, updated_at: 1 }]);
  tasks.listMyTasks.mockResolvedValue([]);
  tasks.getCourse.mockResolvedValue({ id: "crs_1", title: "Physics", description: "", status: "draft", published_at: null, tasks: [] });
  render(<CourseAuthorView isAdmin={false} onOpenTask={() => undefined} />);
  fireEvent.click(await screen.findByRole("button", { name: /Physics/ }));
  await screen.findByTestId("course-editor");
  expect(screen.queryByTestId("hand-over")).toBeNull();
  expect(admin.listUsers).not.toHaveBeenCalled();
});

// ── #6 ──

const joinedHome = home({
  joined: [
    { kind: "practice_set", id: "set_9", title: "Optics drill", joined_at: 2, study: { kind: "practice_set", id: "set_9" } },
    { kind: "course", id: "crs_9", title: "Mechanics", joined_at: 1, study: { kind: "course", id: "crs_9" } },
  ],
  announcements: [{ id: "a1", author: "tina", text: "Exam on Friday", pinned: false, created_at: 1, kind: "course", item_id: "crs_9", item_title: "Mechanics" }],
});

it("shows a teacher what they joined and the announcements on the workbench", async () => {
  auth.value = teacher;
  workbench.getWorkbench.mockResolvedValue({ to_grade: [], attention: [], attention_total: 0, sent: [] });
  tasks.getHome.mockResolvedValue(joinedHome);
  render(<TeacherWorkbench />);
  const joined = await screen.findByTestId("home-joined");
  expect(within(joined).getByRole("link", { name: /Optics drill/ }).getAttribute("href")).toBe("/practice-sets?set=set_9");
  expect(within(joined).getByRole("link", { name: /Mechanics/ }).getAttribute("href")).toBe("/tasks?course=crs_9");
  expect(screen.getByText("Exam on Friday")).toBeTruthy();
});

it("shows an administrator what they joined, the announcements and the hand-over hint", async () => {
  auth.value = administrator;
  tasks.getHome.mockResolvedValue(joinedHome);
  render(<AdminHome />);
  expect(await screen.findByTestId("home-joined")).toBeTruthy();
  expect(screen.getByText("Exam on Friday")).toBeTruthy();
  expect(screen.getByTestId("admin-hand-over-hint").textContent).toContain(
    "Courses and tasks handed to you can be handed to a teacher who grades them.");
});

it("shows a student what they joined, under neutral wording", async () => {
  tasks.getHome.mockResolvedValue(joinedHome);
  render(<StudentHome />);
  expect(await screen.findByTestId("home-joined")).toBeTruthy();
  expect(screen.getByRole("heading", { name: "From my courses" })).toBeTruthy();
  expect(screen.getByText("Tasks from your courses, what is new in the market, and what you are learning yourself.")).toBeTruthy();
});

// ── #7 ──

it("sends a teacher's /start?task= to their own task or to the courses they joined", async () => {
  expect(authorTaskHref("t_1", true)).toBe("/tasks?task=t_1");
  expect(authorTaskHref("t_1", false)).toBe("/tasks?tab=joined&task=t_1");
  expect(authorTaskHref("practice_set:set_1", false)).toBe("/practice-sets?set=set_1");
  expect(authorTaskHref("mastery_path:tpl_1", false)).toContain("assigned_tpl_1");

  auth.value = teacher;
  search.value = "task=t_other";
  tasks.listMyTasks.mockResolvedValue([{ id: "t_mine" }]);
  const { unmount } = render(<HomeRoute />);
  await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/tasks?tab=joined&task=t_other"));
  unmount();

  router.replace.mockReset();
  search.value = "task=t_mine";
  render(<HomeRoute />);
  await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/tasks?task=t_mine"));
});

it("opens the task of a joined course for a teacher from ?tab=joined&task=", async () => {
  auth.value = teacher;
  search.value = "tab=joined&task=t_other";
  tasks.listAssignedTasks.mockResolvedValue([]);
  tasks.getAssignedTask.mockResolvedValue({ key: "t_other", title: "Lab report" } as AssignedTask);
  render(<TasksHub />);
  expect(screen.getByRole("tab", { name: "Courses I joined" }).getAttribute("aria-selected")).toBe("true");
  expect(await screen.findByText("open Lab report")).toBeTruthy();
  expect(tasks.getAssignedTask).toHaveBeenCalledWith("t_other");
});

it("links a teacher's calendar by whose task it is", async () => {
  auth.value = teacher;
  const at = new Date().getTime() / 1000;
  tasks.listCalendar.mockResolvedValue([
    { key: "t_mine", title: "Mine", kind: "homework", state: "published", type: "due", at },
    { key: "t_other", title: "Theirs", kind: "homework", state: "open", type: "due", at },
  ]);
  tasks.listMyTasks.mockResolvedValue([{ id: "t_mine" }]);
  render(<TaskCalendar />);
  await waitFor(() =>
    expect(screen.getByRole("link", { name: /Theirs/ }).getAttribute("href")).toBe("/tasks?tab=joined&task=t_other"));
  expect(screen.getByRole("link", { name: /Mine/ }).getAttribute("href")).toBe("/tasks?task=t_mine");
});

// ── #4 ──

it("lets the author comment on a learner's not-mastered point in the report", async () => {
  auth.value = teacher;
  search.value = "kind=mastery_path&id=tpl_1";
  catalog.getLearnerReport.mockResolvedValue({
    kind: "mastery_path", id: "tpl_1", title: "Algebra", period_days: 7, paths: [], practice_sets: [],
    learners: [{ id: "u_sara", username: "sara", overall: { last_activity: 1 }, paths: [], practice_sets: [], tasks: null, flags: [] }],
  });
  catalog.getLearnerDetail.mockResolvedValue({
    username: "sara", tasks: null, practice_sets: [],
    paths: [{ template_id: "tpl_1", title: "Algebra", attempts: { correct: 1, partial: 0, incorrect: 2 },
      knowledge_points: [
        { id: "kp_a", name: "Fractions", mastery: 0.3, mastered: false },
        { id: "kp_b", name: "Integers", mastery: 1, mastered: true },
      ] }],
  });
  comments.writtenComments.mockResolvedValue([]);
  render(<LearnerReportPage />);
  fireEvent.click(await screen.findByRole("button", { name: "sara" }));
  const detail = await screen.findByTestId("learner-detail");
  await waitFor(() => expect(within(detail).getAllByRole("button", { name: "Comment" })).toHaveLength(1));
  expect(comments.writtenComments).toHaveBeenCalledWith("path_point", "tpl_1", "u_sara");
});

// ── #9 ──

const listed = (over: Partial<CatalogEntry>): CatalogEntry => ({
  kind: "course", id: "c", owner_id: "u_t", status: "listed", title: "", summary: "", stages: [], subjects: [], grades: [],
  difficulty: "", tags: [], approved_once: true, migrated: false, reason: "", submitted_at: null, listed_at: 1, updated_at: 1,
  needs_classification: false, learners: 0, author: "tina", author_role: "teacher", ...over,
});

it("marks listings that need classification in the market tab and filters to them", async () => {
  auth.value = administrator;
  catalog.listReviewQueue.mockResolvedValue([]);
  catalog.listReviewListed.mockResolvedValue([
    listed({ id: "c1", title: "Migrated optics", needs_classification: true }),
    listed({ id: "c2", title: "Classified waves" }),
  ]);
  render(<ReviewDesk />);
  fireEvent.click(screen.getByRole("tab", { name: "In the market" }));
  const list = await screen.findByRole("list", { name: "Listings" });
  expect(within(list).getAllByTestId("needs-classification")).toHaveLength(1);
  expect(within(list).getByText("Classified waves")).toBeTruthy();
  fireEvent.click(screen.getByRole("checkbox", { name: "Only those needing classification" }));
  expect(within(screen.getByRole("list", { name: "Listings" })).queryByText("Classified waves")).toBeNull();
  expect(screen.getByText("Migrated optics")).toBeTruthy();
});

// ── #10 ──

const note = (kind: string, params: Record<string, unknown>): AppNotification =>
  ({ id: "n", kind, params, link: "", count: 1, created_at: 1, read: false });

it("words a changed score and withdrawn material without teachers or classes", () => {
  expect(notificationText(note("practice_regraded", { title: "Waves" }), t)).toBe("Your score on “Waves” was changed");
  expect(notificationText(note("material_withdrawn", { title: "Optics path", material: "Lenses", count: 7 }), t))
    .toBe("Material “Lenses” cited by “Optics path” is no longer available (7 learners affected)");
});
