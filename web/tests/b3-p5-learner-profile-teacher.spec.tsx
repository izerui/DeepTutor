import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LearnerProfileCard } from "@/components/space/learning/LearnerProfileCard";
import { initI18n } from "@/i18n/init";

initI18n("en");

describe("B3-6b LearnerProfileCard teacher path", () => {
  it("does not mention 'designs the outline' when teacherPath is true", () => {
    render(<LearnerProfileCard profile={null} teacherPath />);

    expect(
      screen.queryByText(/designs the outline/),
    ).toBeNull();
  });

  it("still shows the outline text for non-teacher paths", () => {
    render(<LearnerProfileCard profile={null} />);

    expect(
      screen.getByText(/designs the outline/),
    ).toBeTruthy();
  });
});
