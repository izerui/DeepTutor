import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import CourseNextStep from "@/components/courses/CourseNextStep";
import { initI18n } from "@/i18n/init";
import type { CourseState } from "@/lib/courses-api";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
initI18n("en");

function state(overrides: Partial<CourseState> = {}): CourseState {
  return {
    course: {} as CourseState["course"],
    resources: [{ kind: "kb", id: "kb", available: true, detail: {} } as unknown as CourseState["resources"][number]],
    sessions: { active: 0, archived: 0, recent: [] },
    mastery: { paths: [] },
    question_bank: { total: 0, wrong: 0, weak_categories: [] },
    reading: { workspaces: [{ workspace_id: "ws", title: "Notes", materials: 2 }] },
    syllabus: {
      total: 3,
      covered: 1,
      next: { id: "u2", title: "Limits", position: 2 },
      units: [],
    },
    ...overrides,
  };
}

describe("course next step", () => {
  it("points at the next uncovered syllabus unit when nothing is going wrong", () => {
    render(<CourseNextStep state={state()} courseId="c1" />);
    expect(screen.getByText("Next in the syllabus: Limits")).toBeInTheDocument();
    expect(screen.getByText("1 of 3 units covered")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Study this unit/ })).toBeInTheDocument();
  });

  it("still sends the learner to open mistakes first", () => {
    render(
      <CourseNextStep
        state={state({ question_bank: { total: 4, wrong: 2, weak_categories: [{ name: "Derivatives", wrong: 2 }] } })}
        courseId="c1"
      />,
    );
    expect(screen.getByText("Derivatives is where you are losing marks")).toBeInTheDocument();
  });

  it("falls back to reading once the syllabus is fully covered", () => {
    render(
      <CourseNextStep
        state={state({ syllabus: { total: 3, covered: 3, next: null, units: [] } })}
        courseId="c1"
      />,
    );
    expect(screen.getByText("Notes is open and waiting")).toBeInTheDocument();
  });
});
