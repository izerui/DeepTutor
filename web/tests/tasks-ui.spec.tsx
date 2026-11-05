import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

const { api, notes, t } = vi.hoisted(() => ({
  api: { markTaskDone: vi.fn(), getHome: vi.fn(), getAssignedTask: vi.fn(), listAssignedTasks: vi.fn() },
  notes: { listNotifications: vi.fn(), unreadNotifications: vi.fn(), markNotificationsRead: vi.fn() },
  t: (key: string, values?: Record<string, unknown>) =>
    key.replace(/\{\{(\w+)\}\}/g, (_: string, name: string) => String(values?.[name] ?? "")),
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t }) }));
vi.mock("@/lib/tasks-api", () => api);
vi.mock("@/lib/workbench-api", () => ({
  getMyAiUsage: vi.fn(() => Promise.resolve(null)),
  getWorkbench: vi.fn(() => Promise.resolve({ role: "learner", sent: [] })),
}));
vi.mock("@/components/catalog/MyListings", () => ({ MyListings: () => null }));
vi.mock("@/lib/notifications-api", () => notes);
vi.mock("@/hooks/useAuthStatus", () => ({ useAuthStatus: () => ({ isAdmin: false, loading: false }) }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), useSearchParams: () => new URLSearchParams() }));

import LearnerHome from "@/components/home/LearnerHome";
import { NotificationBell } from "@/components/notifications/NotificationBell";
import { StudentTaskDetail } from "@/components/tasks/StudentTaskDetail";
import type { AssignedTask } from "@/lib/tasks-api";

const note: AssignedTask = {
  key: "task_1", task_id: "task_1", kind: "note", title: "Essay", description: "Write about waves.",
  target_id: "", target_title: "", settings: {}, state: "open",
  completed_at: null, provenance: { kind: "shared", name: "tina" },
};

beforeEach(() => {
  Object.values(api).forEach((fn) => fn.mockReset());
  Object.values(notes).forEach((fn) => fn.mockReset());
});

it("marks a note done in place and offers no hand-in form", async () => {
  api.markTaskDone.mockResolvedValue({ ...note, state: "completed", completed_at: 1 });
  const changed = vi.fn();
  render(<StudentTaskDetail task={note} onChanged={changed} />);
  expect(screen.getByText("Published by tina")).toBeTruthy();
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(screen.queryByRole("button", { name: "Hand in" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Mark as done" }));
  await waitFor(() => expect(api.markTaskDone).toHaveBeenCalledWith("task_1"));
  expect(changed.mock.calls[0][0].state).toBe("completed");
});

it("words notifications and marks them all read", async () => {
  notes.unreadNotifications.mockResolvedValue(2);
  notes.listNotifications.mockResolvedValue({ unread: 2, notifications: [
    { id: "n1", kind: "task_published", params: { teacher: "tina", title: "Essay" }, link: "/start?task=task_1", count: 1, created_at: 1, read: false },
    { id: "n2", kind: "content_announcement", params: { teacher: "tina", title: "Waves" }, link: "/tasks?course=crs_1", count: 1, created_at: 2, read: false },
  ] });
  notes.markNotificationsRead.mockResolvedValue(0);
  render(<NotificationBell />);
  const bell = await screen.findByRole("button", { name: "Notifications (2 unread)" });
  fireEvent.click(bell);
  expect(await screen.findByText(/tina assigned “Essay”/)).toBeTruthy();
  expect(screen.getByText(/tina posted an announcement in “Waves”/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Mark all read" }));
  await waitFor(() => expect(notes.markNotificationsRead).toHaveBeenCalledWith());
});

it("keeps what teachers assigned apart from the student's own learning", async () => {
  api.getHome.mockResolvedValue({
    assigned: [note], history_count: 0,
    market: [{ kind: "book", id: "b1", title: "School handbook", author: "", author_role: "admin" }],
    mine: [{ path_id: "topic_1", name: "My algebra", mastered: 2, total: 5, updated_at: 1 }],
  });
  render(<LearnerHome />);
  expect(await screen.findByRole("heading", { name: "From my courses" })).toBeTruthy();
  expect(screen.getByText("Essay")).toBeTruthy();
  expect(screen.getByRole("heading", { name: "New in the market" })).toBeTruthy();
  expect(screen.getByRole("link", { name: /School handbook/ }).getAttribute("href")).toBe("/market/item?kind=book&id=b1");
  expect(screen.getByRole("heading", { name: "What I am learning" })).toBeTruthy();
  expect(screen.getByText("My algebra")).toBeTruthy();
});
