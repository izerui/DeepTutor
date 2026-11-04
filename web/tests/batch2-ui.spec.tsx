import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

// Spec 2026-11-03 batch 2 §10: comments, announcements, courses, opening times, the calendar.
const { tasks, comments, t } = vi.hoisted(() => ({
  tasks: { getHome: vi.fn(), getAssignedTask: vi.fn(), listAssignedTasks: vi.fn(), listAssignedCourses: vi.fn(), listCalendar: vi.fn(), markTaskDone: vi.fn(), submitHomework: vi.fn() },
  comments: { myComments: vi.fn(), writtenComments: vi.fn(), writeComment: vi.fn(), deleteComment: vi.fn() },
  t: (key: string, values?: Record<string, unknown>) =>
    key.replace(/\{\{(\w+)\}\}/g, (_: string, name: string) => String(values?.[name] ?? "")),
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t, i18n: { language: "en" } }) }));
vi.mock("@/lib/tasks-api", () => tasks);
vi.mock("@/lib/comments-api", () => comments);
vi.mock("@/hooks/useAuthStatus", () => ({ useAuthStatus: () => ({ isAdmin: false, isTeacher: false, loading: false }) }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), useSearchParams: () => new URLSearchParams() }));

import { CommentEditor, MyComments } from "@/components/comments/Comments";
import StudentHome from "@/components/home/StudentHome";
import { StudentCourses } from "@/components/tasks/Courses";
import { StudentTaskDetail } from "@/components/tasks/StudentTaskDetail";
import TaskCalendar from "@/components/tasks/TaskCalendar";
import type { AssignedTask } from "@/lib/tasks-api";

const later = Date.UTC(2030, 0, 15, 9) / 1000;
const note: AssignedTask = {
  key: "task_2", task_id: "task_2", kind: "note", title: "Revise", description: "", target_id: "",
  target_title: "", settings: {}, due_at: null, open_at: later, state: "not_open", completed_at: null,
  late: false, provenance: { kind: "teacher", name: "tina" }, submission: null,
};

beforeEach(() => {
  [tasks, comments].forEach((group) => Object.values(group).forEach((fn) => fn.mockReset()));
});

it("lets the teacher write one comment and save it again as an edit", async () => {
  comments.writtenComments.mockResolvedValue([]);
  comments.writeComment.mockResolvedValue({ id: "c1", ref: "ps_1:sub_1", text: "Check the sum." });
  render(<CommentEditor kind="practice_submission" subject="ps_1" target="sub_1" studentId="u_sara" />);
  fireEvent.click(await screen.findByRole("button", { name: "Comment" }));
  fireEvent.change(screen.getByLabelText("Comment to the student"), { target: { value: " Check the sum. " } });
  fireEvent.click(screen.getByRole("button", { name: "Save comment" }));
  await waitFor(() => expect(comments.writeComment).toHaveBeenCalledWith("practice_submission", "ps_1:sub_1", "u_sara", "Check the sum."));
  expect(await screen.findByRole("button", { name: "Delete" })).toBeTruthy();
});

it("shows the student their teachers' comments, naming the point", async () => {
  comments.myComments.mockResolvedValue([
    { id: "c1", author: "tina", ref: "tpl_1:kp1", text: "Limits again.", updated_at: 1 },
  ]);
  render(<MyComments kind="path_point" subject="tpl_1" label={(kp) => (kp === "kp1" ? "Limits" : "")} />);
  expect(await screen.findByText("Limits again.")).toBeTruthy();
  expect(screen.getByText("Limits:")).toBeTruthy();
  expect(comments.myComments).toHaveBeenCalledWith("path_point", "tpl_1");
});

it("still shows an older class announcement on the home, by its class name (platform batch 3 §3)", async () => {
  tasks.getHome.mockResolvedValue({
    assigned: [], history_count: 0, can_create_path: false, market: [], mine: [],
    announcements: [{ id: "a1", class_id: "cls_1", class_name: "Physics 1", author: "", text: "Bring a calculator.", pinned: true, created_at: 1, updated_at: 1, can_edit: false }],
  });
  render(<StudentHome />);
  expect(await screen.findByRole("heading", { name: "From my teachers" })).toBeTruthy();
  expect(screen.queryByRole("region", { name: "Class announcements" })).toBeNull();
  const block = screen.getByRole("region", { name: "Announcements" });
  expect(within(block).getByText("Bring a calculator.")).toBeTruthy();
  expect(within(block).queryByRole("link", { name: "Physics 1" })).toBeNull();
});

it("shows a task that is not open yet with only its opening and nothing to do", () => {
  render(<StudentTaskDetail task={note} onChanged={vi.fn()} />);
  expect(screen.getByText("Not open yet")).toBeTruthy();
  expect(screen.getByText(/^Opens /)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Mark as done" })).toBeNull();
});

it("lists a teacher's course with its tasks in order and opens the one from the link", async () => {
  tasks.listAssignedCourses.mockResolvedValue([{
    id: "crs_1", title: "Waves, term 1", description: "Read in order.", provenance: { kind: "teacher", name: "tina" }, published_at: 1,
    tasks: [{ ...note, key: "task_1", task_id: "task_1", title: "Intro", state: "completed", open_at: null }, note],
  }]);
  const open = vi.fn();
  render(<StudentCourses courseId="crs_1" onOpenTask={open} />);
  expect(await screen.findByText("Waves, term 1")).toBeTruthy();
  expect(screen.getByText("1 / 2 done")).toBeTruthy();
  const items = screen.getAllByRole("listitem").map((li) => li.textContent);
  expect(items[0]).toContain("Intro");
  expect(items[1]).toContain("Revise");
  expect(items[1]).toContain("Not open yet");
  fireEvent.click(screen.getByRole("button", { name: /Revise/ }));
  expect(open).toHaveBeenCalledWith(note);
});

it("puts openings and deadlines on their days in the calendar", async () => {
  const now = new Date();
  const day = (d: number, h: number) => new Date(now.getFullYear(), now.getMonth(), d, h).getTime() / 1000;
  tasks.listCalendar.mockResolvedValue([
    { key: "task_1", title: "Lab report", kind: "note", state: "open", type: "open", at: day(3, 9) },
    { key: "task_1", title: "Lab report", kind: "note", state: "open", type: "due", at: day(10, 18) },
  ]);
  render(<TaskCalendar />);
  const opening = await screen.findByRole("link", { name: "Opens · Lab report" });
  expect(opening.getAttribute("href")).toBe("/start?task=task_1");
  expect(screen.getByRole("link", { name: "Due · Lab report" })).toBeTruthy();
});
