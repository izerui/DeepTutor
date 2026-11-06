import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

// Spec 2026-11-03 batch 2 §10: announcements and courses (comments, opening times
// and the calendar are gone, spec 2026-11-05).
const { tasks, t } = vi.hoisted(() => ({
  tasks: { getHome: vi.fn(), getAssignedTask: vi.fn(), listAssignedTasks: vi.fn(), listAssignedCourses: vi.fn(), markTaskDone: vi.fn() },
  t: (key: string, values?: Record<string, unknown>) =>
    key.replace(/\{\{(\w+)\}\}/g, (_: string, name: string) => String(values?.[name] ?? "")),
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t, i18n: { language: "en" } }) }));
vi.mock("@/lib/tasks-api", () => tasks);
vi.mock("@/lib/workbench-api", () => ({
  getMyAiUsage: vi.fn(() => Promise.resolve(null)),
  getWorkbench: vi.fn(() => Promise.resolve({ role: "learner", sent: [] })),
}));
vi.mock("@/components/catalog/MyListings", () => ({ MyListings: () => null }));
vi.mock("@/hooks/useAuthStatus", () => ({ useAuthStatus: () => ({ isAdmin: false, loading: false }) }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), useSearchParams: () => new URLSearchParams() }));

import LearnerHome from "@/components/home/LearnerHome";
import { LearnerCourses } from "@/components/tasks/Courses";
import { LearnerLessonDetail } from "@/components/tasks/LearnerLessonDetail";
import type { AssignedTask } from "@/lib/tasks-api";

const note: AssignedTask = {
  key: "task_2", task_id: "task_2", kind: "note", title: "Revise", description: "", target_id: "",
  target_title: "", settings: {}, state: "open", completed_at: null,
  provenance: { kind: "shared", name: "tina" },
};

beforeEach(() => {
  Object.values(tasks).forEach((fn) => fn.mockReset());
});

it("still shows an older class announcement on the home, by its class name (platform batch 3 §3)", async () => {
  tasks.getHome.mockResolvedValue({
    assigned: [], history_count: 0, market: [], mine: [],
    announcements: [{ id: "a1", class_id: "cls_1", class_name: "Physics 1", author: "", text: "Bring a calculator.", pinned: true, created_at: 1, updated_at: 1, can_edit: false }],
  });
  render(<LearnerHome />);
  expect(await screen.findByRole("heading", { name: "From my courses" })).toBeTruthy();
  expect(screen.queryByRole("region", { name: "Class announcements" })).toBeNull();
  const block = screen.getByRole("region", { name: "Announcements" });
  expect(within(block).getByText("Bring a calculator.")).toBeTruthy();
  expect(within(block).queryByRole("link", { name: "Physics 1" })).toBeNull();
});

it("shows a task with no deadline or opening, ready to do", () => {
  render(<LearnerLessonDetail task={note} onChanged={vi.fn()} />);
  expect(screen.getByText("To do")).toBeTruthy();
  expect(screen.queryByText(/^Due |^Opens /)).toBeNull();
  expect(screen.getByRole("button", { name: "Mark as done" })).toBeTruthy();
});

it("lists an author's course with its tasks in order and opens the one from the link", async () => {
  tasks.listAssignedCourses.mockResolvedValue([{
    id: "crs_1", title: "Waves, term 1", description: "Read in order.", provenance: { kind: "shared", name: "tina" }, published_at: 1,
    tasks: [{ ...note, key: "task_1", task_id: "task_1", title: "Intro", state: "completed" }, note],
  }]);
  const open = vi.fn();
  render(<LearnerCourses courseId="crs_1" onOpenTask={open} />);
  expect(await screen.findByText("Waves, term 1")).toBeTruthy();
  expect(screen.getByText("1 / 2 done")).toBeTruthy();
  const items = screen.getAllByRole("listitem").map((li) => li.textContent);
  expect(items[0]).toContain("Intro");
  expect(items[1]).toContain("Revise");
  expect(items[1]).toContain("To do");
  fireEvent.click(screen.getByRole("button", { name: /Revise/ }));
  expect(open).toHaveBeenCalledWith(note);
});
