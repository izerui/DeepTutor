import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

// Spec 2026-11-03 batch 4 §7: the new notices, own courses on the home page.
const { tasks, t } = vi.hoisted(() => ({
  tasks: { getHome: vi.fn(), getAssignedTask: vi.fn(), listAssignedTasks: vi.fn(), markTaskDone: vi.fn() },
  t: (key: string, values?: Record<string, unknown>) =>
    key.replace(/\{\{(\w+)\}\}/g, (_: string, name: string) => String(values?.[name] ?? "")),
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t, i18n: { language: "en" } }) }));
vi.mock("@/lib/tasks-api", () => tasks);
vi.mock("@/lib/workbench-api", () => ({ getMyAiUsage: vi.fn(() => Promise.resolve({ limit: 0, used: 0, remaining_percent: null })) }));
vi.mock("@/hooks/useAuthStatus", () => ({ useAuthStatus: () => ({ isAdmin: false, isTeacher: false, loading: false }) }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }), useSearchParams: () => new URLSearchParams() }));

import StudentHome from "@/components/home/StudentHome";
import { notificationText } from "@/components/notifications/NotificationBell";
import type { AppNotification } from "@/lib/notifications-api";

const note = (kind: string, params: Record<string, unknown>): AppNotification =>
  ({ id: "n", kind, params, link: "", count: 1, created_at: 1, read: false }) as AppNotification;

it("words the hand-over summary and the withdrawn-material notice", () => {
  expect(notificationText(note("content_transferred", { from: "tom", tasks: 3, practice_sets: 1, books: 0 }), t))
    .toBe("tom's content was handed over to you: 3 tasks, 1 practice sets");
  expect(notificationText(note("material_withdrawn", { title: "Waves", material: "Optics", count: 2 }), t))
    .toBe("Material “Optics” cited by “Waves” is no longer available (2 learners affected)");
});

it("lists one's own courses among what one is learning", async () => {
  tasks.getHome.mockResolvedValue({
    assigned: [], history_count: 0, can_create_path: true, market: [], mine: [],
    mine_courses: [{ id: "crs_1", name: "Algebra", color: "#000", updated_at: 1 }],
  });
  render(<StudentHome />);
  expect((await screen.findByRole("link", { name: /Algebra/ })).getAttribute("href")).toBe("/courses/crs_1");
});
