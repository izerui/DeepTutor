import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

const { api, notes, t } = vi.hoisted(() => ({
  api: { submitHomework: vi.fn(), markTaskDone: vi.fn(), getHome: vi.fn(), getAssignedTask: vi.fn(), listAssignedTasks: vi.fn() },
  notes: { listNotifications: vi.fn(), unreadNotifications: vi.fn(), markNotificationsRead: vi.fn() },
  t: (key: string, values?: Record<string, unknown>) =>
    key.replace(/\{\{(\w+)\}\}/g, (_: string, name: string) => String(values?.[name] ?? "")),
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t }) }));
vi.mock("@/lib/tasks-api", () => api);
vi.mock("@/lib/notifications-api", () => notes);
vi.mock("@/hooks/useAuthStatus", () => ({ useAuthStatus: () => ({ isAdmin: false, isTeacher: false, loading: false }) }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), useSearchParams: () => new URLSearchParams() }));

import StudentHome from "@/components/home/StudentHome";
import { NotificationBell } from "@/components/notifications/NotificationBell";
import { StudentTaskDetail } from "@/components/tasks/StudentTaskDetail";
import type { AssignedTask } from "@/lib/tasks-api";

const homework: AssignedTask = {
  key: "task_1", task_id: "task_1", kind: "homework", title: "Essay", description: "Write about waves.",
  target_id: "", target_title: "", settings: { max_score: 100 }, due_at: null, state: "open",
  completed_at: null, late: false, provenance: { kind: "teacher", name: "tina" }, submission: null,
};

beforeEach(() => {
  Object.values(api).forEach((fn) => fn.mockReset());
  Object.values(notes).forEach((fn) => fn.mockReset());
});

it("hands homework in and shows it waiting for grading", async () => {
  api.submitHomework.mockResolvedValue({
    ...homework, state: "completed",
    submission: { text: "Waves carry energy.", files: [], attempt: 1, submitted_at: 1, late: false,
      status: "submitted", score: null, comment: "", return_reason: "", graded_at: null },
  });
  const changed = vi.fn();
  render(<StudentTaskDetail task={homework} onChanged={changed} />);
  expect(screen.getByText("Published by tina")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Your answer"), { target: { value: "Waves carry energy." } });
  fireEvent.click(screen.getByRole("button", { name: "Hand in" }));
  await waitFor(() => expect(api.submitHomework).toHaveBeenCalledWith("task_1", "Waves carry energy.", []));
  expect(changed.mock.calls[0][0].submission.status).toBe("submitted");
});

it("shows a grade and offers no hand-in once graded", () => {
  render(<StudentTaskDetail task={{ ...homework, state: "completed", submission: {
    text: "x", files: [], attempt: 1, submitted_at: 1, late: false, status: "graded", score: 92,
    comment: "Good.", return_reason: "", graded_at: 2,
  } }} onChanged={vi.fn()} />);
  expect(screen.getByText("Graded: 92 / 100")).toBeTruthy();
  expect(screen.getByText("Good.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Hand in" })).toBeNull();
});

it("words notifications and marks them all read", async () => {
  notes.unreadNotifications.mockResolvedValue(2);
  notes.listNotifications.mockResolvedValue({ unread: 2, notifications: [
    { id: "n1", kind: "task_published", params: { teacher: "tina", title: "Essay" }, link: "/start?task=task_1", count: 1, created_at: 1, read: false },
    { id: "n2", kind: "homework_submitted", params: { title: "Essay" }, link: "/tasks?task=task_1", count: 3, created_at: 2, read: false },
  ] });
  notes.markNotificationsRead.mockResolvedValue(0);
  render(<NotificationBell />);
  const bell = await screen.findByRole("button", { name: "Notifications (2 unread)" });
  fireEvent.click(bell);
  expect(await screen.findByText(/tina assigned “Essay”/)).toBeTruthy();
  expect(screen.getByText(/3 new hand-ins for “Essay”/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Mark all read" }));
  await waitFor(() => expect(notes.markNotificationsRead).toHaveBeenCalledWith());
});

it("keeps what teachers assigned apart from the student's own learning", async () => {
  api.getHome.mockResolvedValue({
    assigned: [homework], history_count: 0, can_create_path: true,
    market: [{ kind: "book", id: "b1", title: "School handbook", author: "", author_role: "admin" }],
    mine: [{ path_id: "topic_1", name: "My algebra", mastered: 2, total: 5, updated_at: 1 }],
  });
  render(<StudentHome />);
  expect(await screen.findByRole("heading", { name: "From my teachers" })).toBeTruthy();
  expect(screen.getByText("Essay")).toBeTruthy();
  expect(screen.getByRole("heading", { name: "New in the market" })).toBeTruthy();
  expect(screen.getByRole("link", { name: /School handbook/ }).getAttribute("href")).toBe("/market/item?kind=book&id=b1");
  expect(screen.getByRole("heading", { name: "What I am learning" })).toBeTruthy();
  expect(screen.getByText("My algebra")).toBeTruthy();
});
