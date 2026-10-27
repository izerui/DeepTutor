import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { expect, it, vi } from "vitest";
import CourseResources from "@/components/courses/CourseResources";
import type { CourseResourceState } from "@/lib/courses-api";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock("next/link", () => ({ default: ({ children }: { children: ReactNode }) => <a>{children}</a> }));
vi.mock("@/shared/ui/Tooltip", () => ({
  default: ({ label, children }: { label: string; children: ReactNode }) => <span data-tip={label}>{children}</span>,
}));
vi.mock("@/lib/courses-api", () => ({ listCourseResourceCandidates: async () => ({}) }));

function row(kind: CourseResourceState["kind"], label: string, reason?: "made_private" | "kb_deleted") {
  return {
    id: label, kind, ref_id: `${kind}:${label}`, label, position: 0, added_at: 0,
    available: false, detail: {}, ...(reason ? { unavailable_reason: reason } : {}),
  } as CourseResourceState;
}

it("explains withdrawn knowledge bases and books, and keeps the generic text otherwise (R1)", () => {
  render(<CourseResources courseId="c1" onAttach={vi.fn()} onDetach={vi.fn()} resources={[
    row("knowledge_base", "Algebra", "made_private"),
    row("book", "Optics", "made_private"),
    row("book", "Gone"),
  ]} />);
  const tips = [...document.querySelectorAll("[data-tip]")].map((node) => node.getAttribute("data-tip"));
  expect(tips).toEqual([
    "Its owner made this knowledge base private — remove the reference, or attach another resource.",
    "Its owner made this book private — remove the reference, or attach another resource.",
    "This target no longer exists — remove the reference, or attach the resource again.",
  ]);
  expect(screen.getAllByText("· Unavailable")).toHaveLength(3);
});
