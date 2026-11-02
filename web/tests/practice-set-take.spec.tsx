import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

const { api, t } = vi.hoisted(() => ({
  api: { takeSet: vi.fn(), submitSet: vi.fn() },
  // Stable like the real i18next ``t``: effects depend on it.
  t: (key: string, values?: Record<string, unknown>) =>
    key.replace(/\{\{(\w+)\}\}/g, (_: string, name: string) => String(values?.[name] ?? "")),
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t }) }));
vi.mock("@/lib/practice-sets-api", () => api);

import { TakeSet } from "@/components/practice-sets/TakeSet";

const view = {
  id: "ps1", title: "Week 1", description: "", status: "published", current_revision: 1,
  due_at: null, reveal: "after_submit", created_at: 0, updated_at: 0, teacher: "tina",
  answers_revealed: false, submissions: [], latest_results: [],
  questions: [
    { question_id: "q1", question: "2 + 2 = ?", question_type: "choice", options: { A: "3", B: "4" }, points: 1 },
    { question_id: "q2", question: "Explain gravity.", question_type: "written", options: {}, points: 2 },
  ],
};

beforeEach(() => {
  api.takeSet.mockReset().mockResolvedValue(view);
  api.submitSet.mockReset().mockResolvedValue({
    submission_id: "s1", score: 1, max_score: 3, late: false, answers_revealed: true,
    results: [
      { question_id: "q1", result: "correct", points: 1, max_points: 1, feedback: "", correct_answer: "B" },
      { question_id: "q2", result: "incorrect", points: 0, max_points: 2, feedback: "Too short", correct_answer: "Mass" },
    ],
  });
});

it("submits the chosen answers and shows per-question results", async () => {
  render(<TakeSet setId="ps1" />);
  fireEvent.click(await screen.findByLabelText(/4/));
  fireEvent.change(screen.getByLabelText("Your answer"), { target: { value: "Things fall" } });
  fireEvent.click(screen.getByRole("button", { name: "Submit" }));
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Score 1 / 3"));
  const [setId, answers] = api.submitSet.mock.calls[0];
  expect(setId).toBe("ps1");
  expect(answers).toEqual({ q1: "B", q2: "Things fall" });
  expect(screen.getByText("Too short")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Answer again" })).toBeTruthy();
});

it("reopening a set shows the last attempt, withheld until the deadline", async () => {
  api.takeSet.mockResolvedValue({
    ...view,
    reveal: "after_due",
    submissions: [{ id: "s1", revision: 1, submitted_at: 1, late: 0, score: null, max_score: 3 }],
    latest_revision: 1,
    latest_results: [
      { question_id: "q1", result: "pending", points: null, max_points: 1, answer: "B" },
      { question_id: "q2", result: "pending", points: null, max_points: 2, answer: "Things fall" },
    ],
  });
  render(<TakeSet setId="ps1" />);
  expect(await screen.findByRole("status")).toHaveTextContent("Submitted");
  expect(screen.getByText("Answers and explanations appear after the deadline.")).toBeTruthy();
  expect(screen.getByLabelText("Your answer")).toHaveValue("Things fall");
  expect(screen.queryByText(/Score/)).toBeNull();
  expect(screen.getByRole("button", { name: "Answer again" })).toBeTruthy();
});
