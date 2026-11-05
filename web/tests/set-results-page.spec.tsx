import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

// Spec 2026-11-05 one-learner-role §2: an administrator reads a course's practice set results, read only.
const { practice, search } = vi.hoisted(() => ({
  practice: { getReport: vi.fn(), downloadSetReport: vi.fn(), getAttempt: vi.fn() },
  search: { value: "set=ps_1&course=crs_1" },
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      key.replace(/\{\{(\w+)\}\}/g, (_: string, name: string) => String(values?.[name] ?? "")),
  }),
}));
vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams(search.value) }));
vi.mock("@/lib/practice-sets-api", async () => ({
  ...(await vi.importActual<typeof import("@/lib/practice-sets-api")>("@/lib/practice-sets-api")),
  ...practice,
}));

import SetResultsPage from "@/components/practice-sets/SetResultsPage";

it("opens the results at once, read only, with a way back to the report", async () => {
  practice.getReport.mockResolvedValue({
    set_id: "ps_1", title: "Week 1 quiz",
    students: [{ id: "u_sara", username: "sara", completed: true, attempts: 1, first_score: 2, best_score: 2,
      best_max_score: 2, max_score: 2, minutes: null, submitted_at: 1, latest_submission_id: "sub_1", ungraded: 0 }],
    questions: [],
  });
  render(<SetResultsPage />);
  expect(await screen.findByText("sara")).toBeTruthy();
  expect(practice.getReport).toHaveBeenCalledWith("ps_1");
  expect(screen.getByRole("button", { name: "Review answers" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "View results" })).toBeNull();
  expect(screen.queryByRole("spinbutton")).toBeNull();
  expect(screen.getByRole("link", { name: "Learning report" }).getAttribute("href")).toBe("/reports?kind=course&id=crs_1");
});
