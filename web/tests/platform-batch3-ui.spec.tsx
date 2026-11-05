import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

// Platform batch 3: learning reports, announcements and "needs attention" for
// courses and Mastery Paths.
const { catalog, practice, workbench, tasks, push, search, t } = vi.hoisted(() => ({
  catalog: {
    getLearnerReport: vi.fn(),
    getReportPoints: vi.fn(),
    getLearnerDetail: vi.fn(),
    listItemAnnouncements: vi.fn(),
    postItemAnnouncement: vi.fn(),
    updateItemAnnouncement: vi.fn(),
    deleteItemAnnouncement: vi.fn(),
    downloadReport: vi.fn(),
  },
  practice: { remedialFromPath: vi.fn() },
  workbench: { getWorkbench: vi.fn() },
  tasks: { getHome: vi.fn() },
  push: vi.fn(),
  search: { value: "" },
  t: (key: string, values?: Record<string, unknown>) =>
    key.replace(/\{\{(\w+)\}\}/g, (_: string, name: string) => String(values?.[name] ?? "")),
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t, i18n: { language: "en" } }) }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn() }),
  usePathname: () => "/reports",
  useSearchParams: () => new URLSearchParams(search.value),
}));
vi.mock("@/lib/catalog-api", async () => ({
  ...(await vi.importActual<typeof import("@/lib/catalog-api")>("@/lib/catalog-api")),
  ...catalog,
}));
vi.mock("@/lib/practice-sets-api", () => practice);
vi.mock("@/lib/workbench-api", () => ({
  ...workbench,
  getSchoolAiLimit: vi.fn(() => Promise.resolve(0)), setSchoolAiLimit: vi.fn(), getMyAiUsage: vi.fn(() => Promise.resolve(null)),
  AI_LIMIT_PRESETS: [{ tokens: 0, label: "No limit" }],
}));
vi.mock("@/lib/tasks-api", async () => ({
  ...(await vi.importActual<typeof import("@/lib/tasks-api")>("@/lib/tasks-api")),
  ...tasks,
}));
vi.mock("@/components/catalog/MyListings", () => ({ MyListings: () => null }));
vi.mock("@/components/catalog/AdminNav", () => ({ usePendingReviews: () => 0 }));

import { AnnouncementsPanel } from "@/components/catalog/Announcements";
import LearnerReportPage from "@/components/catalog/LearnerReport";
import StudentHome from "@/components/home/StudentHome";
import { TeacherWorkbench } from "@/components/home/Workbench";
import { notificationText } from "@/components/notifications/NotificationBell";
import { takeSuggested } from "@/components/practice-sets/SetReview";
import type { ContentAnnouncement, LearnerReport, LearnerRow } from "@/lib/catalog-api";
import type { AppNotification } from "@/lib/notifications-api";

beforeEach(() => {
  [catalog, practice, workbench, tasks].forEach((group) =>
    Object.values(group).forEach((fn) => (fn as ReturnType<typeof vi.fn>).mockReset()));
  push.mockReset();
  search.value = "";
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

const overall = { last_activity: 1_700_000_000, period_answers: 12, period_tasks_done: 3 };
const row = (over: Partial<LearnerRow> = {}): LearnerRow => ({
  id: "u_sara", username: "sara", overall,
  paths: [{ template_id: "tpl_1", title: "Algebra", revision: 1, kp_total: 4, kp_mastered: 1,
    attempts: { correct: 3, partial: 0, incorrect: 2 }, last_activity: 1 }],
  practice_sets: [], tasks: null, flags: [], ...over,
});
const courseReport: LearnerReport = {
  kind: "course", id: "crs_1", title: "Physics", period_days: 7,
  paths: [{ template_id: "tpl_1", title: "Algebra" }], practice_sets: ["ps_1"],
  learners: [
    row({
      tasks: { done: 2, total: 5 },
      practice_sets: [{ set_id: "ps_1", title: "Week 1 quiz", attempts: 2, first_score: 3, best_score: 4, best_max_score: 5,
        max_score: 5, late: false, last_submitted_at: 1 }],
      flags: [{ kind: "overdue", count: 2 }],
    }),
    row({ id: "u_tom", username: "tom", flags: [] }),
  ],
};
const points = {
  template_id: "tpl_1", title: "Algebra",
  points: [
    { id: "kp_1", name: "Fractions", students: 2, mastered: 0, not_mastered: ["u_sara", "u_tom"] },
    { id: "kp_2", name: "Powers", students: 2, mastered: 2, not_mastered: [] },
  ],
};

it("reports a course: tasks, practice score, flags and an export link, no homework column", async () => {
  search.value = "kind=course&id=crs_1";
  catalog.getLearnerReport.mockResolvedValue(courseReport);
  catalog.getReportPoints.mockResolvedValue(points);
  render(<LearnerReportPage />);
  expect(await screen.findByRole("heading", { name: "Physics" })).toBeTruthy();
  // The period defaults to the last 7 days.
  expect(catalog.getLearnerReport).toHaveBeenCalledWith("course", "crs_1", 7);
  const sara = screen.getByRole("button", { name: "sara" }).closest("tr")!;
  expect(within(sara).getByText("2 / 5")).toBeTruthy();
  expect(screen.queryByText(/Homework/)).toBeNull();
  expect(within(sara).getByText("4 / 5")).toBeTruthy();
  expect(within(sara).getByText("1 / 4")).toBeTruthy();
  expect(within(sara).getByText("2 tasks overdue")).toBeTruthy();
  // Answers and tasks done in the period, on this course only.
  expect(screen.getByText("Answers in period")).toBeTruthy();
  expect(screen.getByText("Tasks done in period")).toBeTruthy();
  expect(within(sara).getByText("12")).toBeTruthy();
  expect(within(sara).getByText("3")).toBeTruthy();
  expect(screen.getByText("Only what happened in this course is shown.")).toBeTruthy();
  // Follow-ups §7: the CSV is fetched with the sign-in credentials, not a bare link.
  catalog.downloadReport.mockResolvedValue(undefined);
  fireEvent.click(screen.getByRole("button", { name: "Export CSV" }));
  await waitFor(() => expect(catalog.downloadReport).toHaveBeenCalledWith("course", "crs_1", 7));
  // Follow-ups §3: a practice set opened from a course's report carries the course.
  expect(screen.getByRole("link", { name: "Week 1 quiz" }).getAttribute("href")).toBe("/practice-sets?set=ps_1&course=crs_1");

  fireEvent.click(screen.getByLabelText("Only learners who need attention"));
  expect(screen.queryByRole("button", { name: "tom" })).toBeNull();

  // The report keeps to this item: no study time from elsewhere.
  expect(screen.queryByText("Study time in period")).toBeNull();

  // Another period reloads the report, and the export follows it.
  fireEvent.change(screen.getByLabelText("Period"), { target: { value: "30" } });
  await waitFor(() => expect(catalog.getLearnerReport).toHaveBeenLastCalledWith("course", "crs_1", 30));
  fireEvent.change(screen.getByLabelText("Period"), { target: { value: "" } });
  await waitFor(() => expect(catalog.getLearnerReport).toHaveBeenLastCalledWith("course", "crs_1", null));
  fireEvent.click(screen.getByRole("button", { name: "Export CSV" }));
  await waitFor(() => expect(catalog.downloadReport).toHaveBeenLastCalledWith("course", "crs_1", null));
});

it("reports a path without the course columns, and shows a learner's detail", async () => {
  search.value = "kind=mastery_path&id=tpl_1";
  catalog.getLearnerReport.mockResolvedValue({
    ...courseReport, kind: "mastery_path", id: "tpl_1", title: "Algebra", practice_sets: [], learners: [row()],
  });
  catalog.getReportPoints.mockResolvedValue(points);
  catalog.getLearnerDetail.mockResolvedValue({
    id: "u_sara", username: "sara", tasks: null, practice_sets: [],
    paths: [{ template_id: "tpl_1", title: "Algebra", revision: 1, kp_total: 2, kp_mastered: 1, average_mastery: 0.5,
      attempts: { correct: 3, partial: 0, incorrect: 2 }, last_activity: 1,
      knowledge_points: [
        { id: "kp_1", name: "Fractions", mastery: 0.3, mastered: false, answers: 5, wrong: 3 },
        { id: "kp_2", name: "Powers", mastery: 0.9, mastered: true },
      ] }],
  });
  render(<LearnerReportPage />);
  expect(await screen.findByRole("heading", { name: "Algebra" })).toBeTruthy();
  expect(screen.queryByText("Tasks done")).toBeNull();
  expect(screen.queryByText("Tasks done in period")).toBeNull();
  expect(screen.getByText("Answers in period")).toBeTruthy();
  expect(screen.getByText("Only what happened in this path is shown.")).toBeTruthy();
  expect(screen.queryByText("Practice: best score")).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "sara" }));
  const detail = await screen.findByRole("region", { name: "Details of sara" });
  expect(catalog.getLearnerDetail).toHaveBeenCalledWith("mastery_path", "tpl_1", "u_sara");
  expect(within(detail).getByText("30%")).toBeTruthy();
  expect(within(detail).getByText("5 answers, 3 wrong")).toBeTruthy();
});

it("makes remedial practice on one point for those who have not mastered it, and opens the draft", async () => {
  search.value = "kind=mastery_path&id=tpl_1";
  catalog.getLearnerReport.mockResolvedValue({ ...courseReport, kind: "mastery_path", id: "tpl_1", practice_sets: [] });
  catalog.getReportPoints.mockResolvedValue(points);
  practice.remedialFromPath.mockResolvedValue({ set: { id: "ps_new" }, suggested_students: [{ id: "u_sara", username: "sara" }] });
  render(<LearnerReportPage />);
  const view = await screen.findByRole("region", { name: "Knowledge points of Algebra" });
  await within(view).findByText("Fractions");
  expect(catalog.getReportPoints).toHaveBeenCalledWith("mastery_path", "tpl_1", "tpl_1");
  // A point everyone has mastered offers nothing.
  expect(within(view).getAllByRole("button")).toHaveLength(1);
  fireEvent.click(within(view).getByRole("button", { name: "Remedial practice on Fractions for the 2 who have not mastered it" }));
  await waitFor(() => expect(practice.remedialFromPath).toHaveBeenCalledWith("tpl_1", ["kp_1"], ["u_sara", "u_tom"], "Remedial: "));
  await waitFor(() => expect(push).toHaveBeenCalledWith("/practice-sets?set=ps_new"));
  expect(takeSuggested("ps_new")).toEqual([{ id: "u_sara", username: "sara" }]);
});

it("shows the server's refusal when the viewer is not the item's teacher", async () => {
  search.value = "kind=course&id=crs_1";
  catalog.getLearnerReport.mockRejectedValue(new Error("Only the teacher who made it sees its learning report."));
  render(<LearnerReportPage />);
  expect((await screen.findByRole("alert")).textContent).toBe("Only the teacher who made it sees its learning report.");
});

const announcement = (over: Partial<ContentAnnouncement> = {}): ContentAnnouncement => ({
  id: "an_1", kind: "course", item_id: "crs_1", item_title: "Physics", author: "tina", text: "Lab on Friday.",
  pinned: false, created_at: 1, updated_at: 1, can_edit: true, can_delete: true, ...over,
});

it("lets the author post, edit and delete announcements", async () => {
  catalog.listItemAnnouncements.mockResolvedValue([announcement()]);
  catalog.postItemAnnouncement.mockResolvedValue(announcement({ id: "an_2" }));
  catalog.updateItemAnnouncement.mockResolvedValue(announcement());
  catalog.deleteItemAnnouncement.mockResolvedValue({ deleted: true });
  render(<AnnouncementsPanel kind="course" itemId="crs_1" canPost />);
  expect(await screen.findByText("Lab on Friday.")).toBeTruthy();

  fireEvent.change(screen.getByLabelText("New announcement"), { target: { value: "  Bring goggles.  " } });
  fireEvent.click(screen.getByLabelText("Pin to top"));
  fireEvent.click(screen.getByRole("button", { name: "Post" }));
  await waitFor(() => expect(catalog.postItemAnnouncement).toHaveBeenCalledWith("course", "crs_1", "Bring goggles.", true));

  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Edit announcement"), { target: { value: "Lab on Monday." } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(catalog.updateItemAnnouncement).toHaveBeenCalledWith("an_1", { text: "Lab on Monday." }));

  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  await waitFor(() => expect(catalog.deleteItemAnnouncement).toHaveBeenCalledWith("an_1"));
});

it("offers only what each announcement allows, and learners cannot post", async () => {
  catalog.listItemAnnouncements.mockResolvedValue([
    announcement({ id: "an_1", text: "Read chapter 2.", can_edit: false, can_delete: true }),
    announcement({ id: "an_2", text: "Quiz soon.", can_edit: false, can_delete: false, pinned: true }),
  ]);
  render(<AnnouncementsPanel kind="mastery_path" itemId="tpl_1" />);
  expect(await screen.findByText("Quiz soon.")).toBeTruthy();
  expect(screen.queryByLabelText("New announcement")).toBeNull();
  expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  expect(screen.getAllByRole("button", { name: "Delete" })).toHaveLength(1);
  expect(screen.getByLabelText("Pinned")).toBeTruthy();
});

it("keeps a learner's panel out of the way when there is nothing to read", async () => {
  catalog.listItemAnnouncements.mockResolvedValue([]);
  const { container } = render(<AnnouncementsPanel kind="course" itemId="crs_1" />);
  await waitFor(() => expect(catalog.listItemAnnouncements).toHaveBeenCalled());
  expect(container.innerHTML).toBe("");
});

it("shows the newest announcements on the home, each linking to its course or path", async () => {
  tasks.getHome.mockResolvedValue({
    assigned: [], history_count: 0, can_create_path: false, market: [], mine: [],
    announcements: [
      announcement({ text: "Lab on Friday." }),
      announcement({ id: "an_2", kind: "mastery_path", item_id: "tpl_1", item_title: "Algebra", text: "New module." }),
    ],
  });
  render(<StudentHome />);
  const block = await screen.findByRole("region", { name: "Announcements" });
  expect(within(block).getByText("Lab on Friday.")).toBeTruthy();
  expect(within(block).getByRole("link", { name: "Physics" }).getAttribute("href")).toBe("/tasks?course=crs_1");
  expect(within(block).getByRole("link", { name: "Algebra" }).getAttribute("href")).toBe("/market/item?kind=mastery_path&id=tpl_1");
});

it("lists learners who need attention on the workbench, linked to the item's report", async () => {
  workbench.getWorkbench.mockResolvedValue({
    role: "teacher", sent: [], classes: [], attention_total: 2,
    attention: [
      { student_id: "u_sara", username: "sara", class_id: "", item: { kind: "course", id: "crs_1", title: "Physics" },
        flags: [{ kind: "inactive", days: 9 }] },
      { student_id: "u_tom", username: "tom", class_id: "", item: null, flags: [{ kind: "overdue", count: 1 }] },
    ],
  });
  tasks.getHome.mockResolvedValue({ mine: [] });
  render(<TeacherWorkbench />);
  const section = (await screen.findByRole("heading", { name: /Learners who need attention/ })).closest("section")!;
  const link = within(section).getByRole("link", { name: /sara/ });
  expect(link.getAttribute("href")).toBe("/reports?kind=course&id=crs_1");
  expect(within(link).getByText("Physics")).toBeTruthy();
  expect(within(link).getByText("No learning for 9 days")).toBeTruthy();
  expect(within(section).getByText("tom").closest("a")).toBeNull();
});

it("words a content announcement notification", () => {
  const note = { id: "n", kind: "content_announcement", params: { content: "course", title: "Physics", teacher: "tina" },
    link: "/tasks?course=crs_1", count: 1, created_at: 1, read: false } as AppNotification;
  expect(notificationText(note, t)).toBe("tina posted an announcement in “Physics”");
});
