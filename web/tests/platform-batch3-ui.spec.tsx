import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

// Platform batch 3: learning reports and announcements for courses and Mastery
// Paths ("needs attention" and teacher remedial practice are gone, spec 2026-11-05).
const { auth, catalog, workbench, tasks, push, search, t } = vi.hoisted(() => ({
  auth: { isAdmin: false, loading: false },
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
  workbench: { getWorkbench: vi.fn() },
  tasks: { getHome: vi.fn() },
  push: vi.fn(),
  search: { value: "" },
  t: (key: string, values?: Record<string, unknown>) =>
    key.replace(/\{\{(\w+)\}\}/g, (_: string, name: string) => String(values?.[name] ?? "")),
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t, i18n: { language: "en" } }) }));
vi.mock("@/hooks/useAuthStatus", () => ({ useAuthStatus: () => auth }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn() }),
  usePathname: () => "/reports",
  useSearchParams: () => new URLSearchParams(search.value),
}));
vi.mock("@/lib/catalog-api", async () => ({
  ...(await vi.importActual<typeof import("@/lib/catalog-api")>("@/lib/catalog-api")),
  ...catalog,
}));
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
import LearnerHome from "@/components/home/LearnerHome";
import { notificationText } from "@/components/notifications/NotificationBell";
import type { ContentAnnouncement, LearnerReport, LearnerRow } from "@/lib/catalog-api";
import type { AppNotification } from "@/lib/notifications-api";

beforeEach(() => {
  [catalog, workbench, tasks].forEach((group) =>
    Object.values(group).forEach((fn) => (fn as ReturnType<typeof vi.fn>).mockReset()));
  push.mockReset();
  search.value = "";
  auth.isAdmin = false;
  workbench.getWorkbench.mockResolvedValue({ role: "learner", sent: [] });
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

const overall = { last_activity: 1_700_000_000, period_answers: 12, period_tasks_done: 3 };
const row = (over: Partial<LearnerRow> = {}): LearnerRow => ({
  id: "u_sara", username: "sara", overall,
  paths: [{ template_id: "tpl_1", title: "Algebra", revision: 1, kp_total: 4, kp_mastered: 1,
    attempts: { correct: 3, partial: 0, incorrect: 2 }, last_activity: 1 }],
  practice_sets: [], tasks: null, ...over,
});
const courseReport: LearnerReport = {
  kind: "course", id: "crs_1", title: "Physics", period_days: 7,
  paths: [{ template_id: "tpl_1", title: "Algebra" }], practice_sets: ["ps_1"],
  learners: [
    row({
      tasks: { done: 2, total: 5 },
      practice_sets: [{ set_id: "ps_1", title: "Week 1 quiz", attempts: 2, first_score: 3, best_score: 4, best_max_score: 5,
        max_score: 5, last_submitted_at: 1 }],
    }),
    row({ id: "u_tom", username: "tom" }),
  ],
};
const points = {
  template_id: "tpl_1", title: "Algebra",
  points: [
    { id: "kp_1", name: "Fractions", students: 2, mastered: 0, not_mastered: ["u_sara", "u_tom"] },
    { id: "kp_2", name: "Powers", students: 2, mastered: 2, not_mastered: [] },
  ],
};

it("reports a course: tasks, practice score and an export link, no homework or attention column", async () => {
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
  expect(screen.queryByText("Needs attention")).toBeNull();
  expect(screen.queryByLabelText("Only learners who need attention")).toBeNull();
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

it("lets an administrator read a course report, without the author's editing links", async () => {
  // Spec 2026-11-05: administrators read every learning report, read-only.
  auth.isAdmin = true;
  search.value = "kind=course&id=crs_1";
  catalog.getLearnerReport.mockResolvedValue(courseReport);
  catalog.getReportPoints.mockResolvedValue(points);
  render(<LearnerReportPage />);
  expect(await screen.findByRole("heading", { name: "Physics" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "sara" })).toBeTruthy();
  // Back goes to the course's introduction, not the author's course editor.
  expect(screen.getByRole("link", { name: "Learning market" }).getAttribute("href")).toBe("/market/item?kind=course&id=crs_1");
  // A practice set's results open read-only, not in its author's editor.
  expect(screen.getByRole("link", { name: "Week 1 quiz" }).getAttribute("href"))
    .toBe("/practice-sets/results?set=ps_1&course=crs_1");
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
  // Nothing to pick, comment on or send to the learner (spec 2026-11-05 §1.1, §1.2).
  expect(within(detail).queryByRole("checkbox")).toBeNull();
  expect(within(detail).queryByRole("button")).toBeNull();
});

it("shows who has mastered each point and offers nothing to send", async () => {
  search.value = "kind=mastery_path&id=tpl_1";
  catalog.getLearnerReport.mockResolvedValue({ ...courseReport, kind: "mastery_path", id: "tpl_1", practice_sets: [] });
  catalog.getReportPoints.mockResolvedValue(points);
  render(<LearnerReportPage />);
  const view = await screen.findByRole("region", { name: "Knowledge points of Algebra" });
  await within(view).findByText("Fractions");
  expect(catalog.getReportPoints).toHaveBeenCalledWith("mastery_path", "tpl_1", "tpl_1");
  expect(within(view).getByText("0 / 2 mastered")).toBeTruthy();
  expect(within(view).queryByRole("button")).toBeNull();
  expect(push).not.toHaveBeenCalled();
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
    assigned: [], history_count: 0, market: [], mine: [],
    announcements: [
      announcement({ text: "Lab on Friday." }),
      announcement({ id: "an_2", kind: "mastery_path", item_id: "tpl_1", item_title: "Algebra", text: "New module." }),
    ],
  });
  render(<LearnerHome />);
  const block = await screen.findByRole("region", { name: "Announcements" });
  expect(within(block).getByText("Lab on Friday.")).toBeTruthy();
  expect(within(block).getByRole("link", { name: "Physics" }).getAttribute("href")).toBe("/tasks?course=crs_1");
  expect(within(block).getByRole("link", { name: "Algebra" }).getAttribute("href")).toBe("/market/item?kind=mastery_path&id=tpl_1");
});

it("has no learners-who-need-attention list on the home", async () => {
  workbench.getWorkbench.mockResolvedValue({ role: "learner", sent: [] });
  tasks.getHome.mockResolvedValue({ assigned: [], history_count: 0, mine: [] });
  render(<LearnerHome />);
  expect(await screen.findByRole("heading", { name: "What I sent out" })).toBeTruthy();
  expect(screen.queryByRole("heading", { name: /need attention/ })).toBeNull();
});

it("words a content announcement notification", () => {
  const note = { id: "n", kind: "content_announcement", params: { content: "course", title: "Physics", teacher: "tina" },
    link: "/tasks?course=crs_1", count: 1, created_at: 1, read: false } as AppNotification;
  expect(notificationText(note, t)).toBe("tina posted an announcement in “Physics”");
});
