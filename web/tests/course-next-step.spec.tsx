import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import CourseNextStep from "@/components/courses/CourseNextStep";
import { initI18n } from "@/i18n/init";
import type { CourseNextStepData, CourseState } from "@/lib/courses-api";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
initI18n("en");

beforeEach(() => push.mockClear());

function state(steps: CourseNextStepData[]): CourseState {
  return {
    course: {} as CourseState["course"],
    resources: [],
    sessions: { active: 0, archived: 0, recent: [] },
    mastery: { paths: [] },
    question_bank: { total: 0, wrong: 0, weak_categories: [] },
    reading: { workspaces: [] },
    syllabus: { total: 0, covered: 0, next: null, units: [] },
    next_steps: steps,
  };
}

const take = () => fireEvent.click(screen.getByRole("button"));

describe("course next step renders the shared ranking", () => {
  it("due practice reviews open the course's practice queue", () => {
    render(<CourseNextStep state={state([{ kind: "practice_review", count: 5 }, { kind: "plan" }])} courseId="c1" />);
    expect(screen.getByText("Review what is due today")).toBeInTheDocument();
    expect(screen.getByText("5 reviews due")).toBeInTheDocument();
    take();
    expect(push.mock.calls[0][0]).toContain("/practice?course=c1");
  });

  it("a path's due reviews open its review mode inside the course", () => {
    render(
      <CourseNextStep
        state={state([{ kind: "path_review", path_id: "p1", name: "Calculus", count: 2 }])}
        courseId="c1"
      />,
    );
    expect(screen.getByText("Calculus")).toBeInTheDocument();
    expect(screen.getByText("2 reviews due")).toBeInTheDocument();
    take();
    expect(push.mock.calls[0][0]).toMatch(/\/p1\/sessions\?mode=review&course=c1/);
  });

  it("open mistakes, then paths carried with their course", () => {
    const { unmount } = render(
      <CourseNextStep state={state([{ kind: "open_mistakes", name: "Derivatives", count: 2 }])} courseId="c1" />,
    );
    expect(screen.getByText("Derivatives is where you are losing marks")).toBeInTheDocument();
    unmount();
    render(
      <CourseNextStep
        state={state([{ kind: "continue_path", path_id: "p1", name: "Calc", done: 1, total: 4, weak_point: "" }])}
        courseId="c1"
      />,
    );
    take();
    expect(push.mock.calls[0][0]).toMatch(/\/p1\/sessions\?course=c1/);
  });

  it("the next uncovered syllabus unit", () => {
    render(
      <CourseNextStep
        state={state([
          { kind: "syllabus_next", unit: { id: "u2", title: "Limits", position: 1 }, covered: 1, total: 3 },
        ])}
        courseId="c1"
      />,
    );
    expect(screen.getByText("Next in the syllabus: Limits")).toBeInTheDocument();
    expect(screen.getByText("1 of 3 units covered")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Study this unit/ })).toBeInTheDocument();
  });

  it("an empty course asks for material", () => {
    render(<CourseNextStep state={state([{ kind: "attach_material" }])} courseId="c1" />);
    expect(screen.getByText("This subject has nothing to work with yet")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });
});
