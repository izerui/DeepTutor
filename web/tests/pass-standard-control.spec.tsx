import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

const { api, auth, t } = vi.hoisted(() => ({
  api: { updateMasteryPassStandard: vi.fn() },
  auth: { isTeacher: false, isAdmin: false },
  t: (key: string) => key,
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t }) }));
vi.mock("@/lib/learning-api", () => api);
vi.mock("@/hooks/useAuthStatus", () => ({ useAuthStatus: () => auth }));

import { PassStandardControl } from "@/components/space/learning/PassStandardControl";
import type { MasteryTopic } from "@/lib/learning-api";

const topic = (pass_standard?: MasteryTopic["pass_standard"]) =>
  ({ path_id: "topic_own", pass_standard }) as MasteryTopic;

beforeEach(() => {
  api.updateMasteryPassStandard.mockReset();
  auth.isTeacher = false;
});

it("lets the learner switch their own path and reports the saved topic", async () => {
  const saved = topic("relaxed");
  api.updateMasteryPassStandard.mockResolvedValue(saved);
  const onSaved = vi.fn();
  render(<PassStandardControl topic={topic()} locked={false} onSaved={onSaved} />);

  // Paths saved before the setting existed read as strict.
  expect(screen.getByRole("radio", { name: "Strict" })).toHaveAttribute("aria-checked", "true");
  fireEvent.click(screen.getByRole("radio", { name: "Relaxed" }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved));
  expect(api.updateMasteryPassStandard).toHaveBeenCalledWith("topic_own", "relaxed");
  expect(screen.queryByText(/students get the new standard/)).toBeNull();
});

it("shows a teacher path's standard read-only", () => {
  render(<PassStandardControl topic={topic("standard")} locked onSaved={vi.fn()} />);
  expect(screen.queryByRole("radio")).toBeNull();
  expect(screen.getByText("Standard")).toBeTruthy();
  expect(screen.getByText(/Set by your teacher/)).toBeTruthy();
});

it("tells teachers that students get a change with the next update", () => {
  auth.isTeacher = true;
  render(<PassStandardControl topic={topic("strict")} locked={false} onSaved={vi.fn()} />);
  expect(screen.getByText(/students get the new standard with your next update/)).toBeTruthy();
});

it("keeps the choice and says so when saving fails", async () => {
  api.updateMasteryPassStandard.mockRejectedValue(new Error("boom"));
  const onSaved = vi.fn();
  render(<PassStandardControl topic={topic("strict")} locked={false} onSaved={onSaved} />);
  fireEvent.click(screen.getByRole("radio", { name: "Standard" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Could not save the pass standard");
  expect(onSaved).not.toHaveBeenCalled();
  expect(screen.getByRole("radio", { name: "Strict" })).toHaveAttribute("aria-checked", "true");
});
