import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

// Spec 2026-11-03 batch 3 §13: review and regrade, options, the workbench,
// navigation by role, AI left, similar questions.
const { practice, workbench, t } = vi.hoisted(() => ({
  practice: { getAttempt: vi.fn(), regradeQuestion: vi.fn() },
  workbench: { getWorkbench: vi.fn(), getMyAiUsage: vi.fn(), similarQuestions: vi.fn(),
    getSchoolAiLimit: vi.fn(), setSchoolAiLimit: vi.fn(), AI_LIMIT_PRESETS: [{ tokens: 0, label: "No limit" }] },
  t: (key: string, values?: Record<string, unknown>) =>
    key.replace(/\{\{(\w+)\}\}/g, (_: string, name: string) => String(values?.[name] ?? "")),
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t, i18n: { language: "en" } }) }));
vi.mock("@/lib/practice-sets-api", () => practice);
vi.mock("@/lib/workbench-api", () => workbench);
vi.mock("@/lib/tasks-api", () => ({ getHome: vi.fn(() => Promise.resolve({ mine: [] })) }));
vi.mock("@/lib/book-api", () => ({ bookApi: { list: vi.fn(() => Promise.resolve({ books: [] })), getSpine: vi.fn() } }));
const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }), useSearchParams: () => new URLSearchParams() }));

import { AiUsageNote } from "@/components/home/AiUsage";
import { TeacherWorkbench } from "@/components/home/Workbench";
import { SimilarQuestionsButton } from "@/components/learning/practice/SimilarQuestions";
import { AttemptReview, OptionBars } from "@/components/practice-sets/SetReview";
import { navVisibleForRole, PRIMARY_NAV } from "@/components/sidebar/nav-entries";

beforeEach(() => {
  [practice, workbench].forEach((group) =>
    Object.values(group).forEach((fn) => typeof fn === "function" && "mockReset" in fn && (fn as ReturnType<typeof vi.fn>).mockReset()));
  push.mockReset();
});

const attempt = {
  id: "sub_1", set_id: "ps_1", revision: 1, student_id: "u_sara", username: "sara", submitted_at: 1,
  score: 1, max_score: 5, regraded_at: null,
  questions: [
    { question_id: "q1", question: "Explain gravity.", question_type: "short_answer", options: {}, correct_answer: "Mass attracts.",
      result: "ungraded", points: 0, max_points: 4, answer: "Things fall.", regraded: false },
  ],
};

it("shows a short answer's text and saves a changed score", async () => {
  practice.getAttempt.mockResolvedValue(attempt);
  practice.regradeQuestion.mockResolvedValue({ ...attempt, score: 4, questions: [{ ...attempt.questions[0], points: 3, result: "partial", regraded: true }] });
  const changed = vi.fn();
  render(<AttemptReview setId="ps_1" submissionId="sub_1" onClose={vi.fn()} onChanged={changed} />);
  expect(await screen.findByText("Things fall.")).toBeTruthy();
  expect(screen.getByText("Not graded")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Points for question 1"), { target: { value: "3" } });
  fireEvent.click(screen.getByRole("button", { name: "Save points" }));
  await waitFor(() => expect(practice.regradeQuestion).toHaveBeenCalledWith("ps_1", "sub_1", "q1", 3));
  expect(await screen.findByText("Changed by you")).toBeTruthy();
  expect(changed).toHaveBeenCalled();
});

it("draws how many picked each option and marks the right one", () => {
  render(<OptionBars question={{
    question_id: "q1", question: "2 + 2", answered: 4, error_rate: 0.25, question_type: "choice",
    options: { A: "3", B: "4" }, correct_answer: "B", option_counts: { A: 1, B: 3 },
  }} />);
  const list = screen.getByRole("list", { name: "Option distribution" });
  expect(within(list).getByText("1 · 25%")).toBeTruthy();
  expect(within(list).getByText("B. 4").className).toContain("font-semibold");
});

it("lays out the workbench: what was sent, no classes, nothing to grade, nobody to watch", async () => {
  workbench.getWorkbench.mockResolvedValue({
    role: "teacher",
    classes: [],
    sent: [{ kind: "practice_set", id: "ps_1", title: "Week 3", done: 1, total: 2, updated_at: 1 }],
  });
  render(<TeacherWorkbench />);
  expect(await screen.findByText("1 / 2 done")).toBeTruthy();
  expect(screen.queryByText("To grade")).toBeNull();
  expect(screen.queryByRole("heading", { name: "My classes" })).toBeNull();
  expect(screen.queryByRole("heading", { name: /need attention/ })).toBeNull();
});

it("shows each role its entries and students only the features allowed to them", () => {
  // Traceability #1: students answer the practice sets they joined.
  expect(navVisibleForRole("/practice-sets", "student", {})).toBe(true);
  expect(navVisibleForRole("/practice-sets", "teacher", null)).toBe(true);
  expect(PRIMARY_NAV.some((entry) => entry.href === "/grading")).toBe(false);
  expect(navVisibleForRole("/partners", "student", { allow_partners: false })).toBe(false);
  expect(navVisibleForRole("/partners", "student", { allow_partners: true })).toBe(true);
  expect(navVisibleForRole("/partners", "teacher", null)).toBe(true);
  expect(navVisibleForRole("/tasks", "student", {})).toBe(true);
});

it("tells a limited student how much AI is left, and nothing without a limit", async () => {
  workbench.getMyAiUsage.mockResolvedValue({ limit: 1000, used: 900, remaining_percent: 10 });
  const { unmount } = render(<AiUsageNote />);
  expect((await screen.findByRole("status")).textContent).toBe("AI left today: 10%");
  unmount();
  workbench.getMyAiUsage.mockResolvedValue({ limit: 0, used: 900, remaining_percent: null });
  render(<AiUsageNote />);
  await waitFor(() => expect(workbench.getMyAiUsage).toHaveBeenCalledTimes(2));
  expect(screen.queryByRole("status")).toBeNull();
});

it("turns chosen mistakes into new questions and starts practising them", async () => {
  workbench.similarQuestions.mockResolvedValue({ entry_ids: [11, 12, 13], title: "Remedial" });
  const made = vi.fn();
  render(<SimilarQuestionsButton ids={[1, 2]} disabled={false} onMade={made} />);
  fireEvent.change(screen.getByLabelText("How many new questions"), { target: { value: "3" } });
  fireEvent.click(screen.getByRole("button", { name: "New questions like these" }));
  await waitFor(() => expect(made).toHaveBeenCalledWith([11, 12, 13]));
  expect(workbench.similarQuestions).toHaveBeenCalledWith([1, 2], 3, "en");
});
