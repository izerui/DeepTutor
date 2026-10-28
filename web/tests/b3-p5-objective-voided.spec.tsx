import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ObjectiveDetail } from "@/components/space/learning/ObjectiveDetail";
import { initI18n } from "@/i18n/init";
import type { ObjectiveReport } from "@/lib/learning-api";

initI18n("en");

function report(overrides: Partial<ObjectiveReport> = {}): ObjectiveReport {
  return {
    id: "kp1",
    name: "Why XOR matters",
    type: "concept",
    module_name: "Module 1",
    status: "learning",
    gate: "qualitative",
    mastered: false,
    assessed_mastered: false,
    mastery_source: "",
    override_note: "",
    mastery: 1,
    threshold: 1,
    attempts: [],
    correct_count: 0,
    explanation: "",
    review: null,
    errors: [],
    ...overrides,
  };
}

describe("B3-6c qualitative gate voided-only attempts", () => {
  it("does not show 'still count as practice' when only voided attempts exist", () => {
    render(
      <ObjectiveDetail
        report={report({
          attempts: [
            {
              question_id: "q1",
              prompt: "Why does XOR matter?",
              answer: "dunno",
              is_correct: false,
              error_type: "",
              at: 1_700_000_000,
              voided: true,
              void_reason: "objective_redefined",
            },
          ],
        })}
        zh={false}
      />,
    );

    expect(
      screen.queryByText(/Your answers here still count as practice/),
    ).toBeNull();
  });

  it("shows 'still count as practice' when a non-voided attempt exists", () => {
    render(
      <ObjectiveDetail
        report={report({
          attempts: [
            {
              question_id: "q1",
              prompt: "Why does XOR matter?",
              answer: "it is not linearly separable",
              is_correct: true,
              error_type: "",
              at: 1_700_000_000,
            },
          ],
          correct_count: 1,
        })}
        zh={false}
      />,
    );

    expect(
      screen.getByText(/Your answers here still count as practice/),
    ).toBeTruthy();
  });
});
