import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import GuardianSettingsPage from "@/features/settings/sections/GuardianSettingsSection";
import { GuardianRelationshipsEditor } from "@/features/multi-user/components/GuardianRelationshipsEditor";
import { apiFetch } from "@/lib/api";
import type { UserRecord } from "@/lib/admin-api";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      key.replace(/\{\{(\w+)\}\}/g, (_, name: string) => String(values?.[name] ?? "")),
  }),
}));
vi.mock("@/features/settings/store/useStagedSettings", () => ({
  useStagedSettings: (_key: string, value: unknown, setter: unknown) => [value, setter],
}));
vi.mock("@/lib/api", () => ({
  apiUrl: (path: string) => `/kaoyan${path}`,
  apiFetch: vi.fn(),
}));
const relationship = {
  id: "legacy", guardian_user_id: "guardian", guardian_username: "Existing guardian",
  learner_user_id: "learner", learner_username: "Learner",
  permissions: ["assign_materials", "view_reports"],
};
const report = {
  learner: { id: "learner", username: "Learner", disabled: false },
  assigned_materials: [{ book_id: "public-a", permission: "read" }, { book_id: "public-b", permission: "read" }],
  grant_summary: { model_count: 1, knowledge_base_count: 0, skill_count: 0 },
};
beforeEach(() => {
  vi.mocked(apiFetch).mockImplementation(async (url, init) => {
    if (String(url).endsWith("/guardian-report")) return Response.json(report);
    if (init?.method === "DELETE" || String(url).endsWith("/credentials/reset")) return Response.json({ ok: true });
    if (init?.method === "POST") return Response.json({ relationship: {
      ...relationship, id: "new", guardian_user_id: "new-guardian", guardian_username: "New guardian",
      permissions: JSON.parse(String(init.body)).permissions,
    } });
    if (String(url).endsWith("/guardianships") || String(url).endsWith("/guardians"))
      return Response.json({ relationships: [structuredClone(relationship)] });
    throw new Error(`Unexpected request: ${url}`);
  });
});

it.each(["revoke", "reset"] as const)("preserves %s confirmation cancel and submit behavior", async kind => {
  render(<GuardianRelationshipsEditor learnerId="learner" learnerUsername="Learner" users={[]} />);
  await screen.findByText("Existing guardian");
  const open = () => {
    if (kind === "reset") {
      fireEvent.change(screen.getByLabelText("New learner password"), { target: { value: "Synthetic-reset-2026!" } });
      fireEvent.click(screen.getByRole("button", { name: "Reset learner credentials" }));
    } else fireEvent.click(screen.getByRole("button", { name: "Revoke access" }));
  };
  open();
  await screen.findByRole("alertdialog");
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  expect(vi.mocked(apiFetch).mock.calls.filter(([, init]) => init?.method)).toHaveLength(0);
  open();
  fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", {
    name: kind === "reset" ? "Reset credentials" : "Revoke access",
  }));
  await screen.findByText(kind === "reset" ? "Learner credentials were reset." : "Guardian access revoked.");
  const writes = vi.mocked(apiFetch).mock.calls.filter(([, init]) => init?.method);
  expect(writes).toHaveLength(1);
  expect(writes[0]).toEqual(kind === "reset" ? [
    "/kaoyan/api/multi-user/learners/learner/credentials/reset",
    { method: "POST", headers: { "Content-Type": "application/json" }, body: '{"new_password":"Synthetic-reset-2026!"}' },
  ] : ["/kaoyan/api/multi-user/guardians/legacy", { method: "DELETE" }]);
});

it("loads a legacy guardian relationship and public count without requesting materials", async () => {
  render(<GuardianSettingsPage />);
  fireEvent.click(await screen.findByRole("button", { name: /Learner\s*View reports/ }));
  const label = await screen.findByText("Public books");
  expect(label.parentElement).toHaveTextContent("2");
  expect(screen.queryByText("Manage materials")).not.toBeInTheDocument();
  expect(screen.queryByText("assign_materials")).not.toBeInTheDocument();
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  expect(vi.mocked(apiFetch).mock.calls.map(([url]) => url)).toEqual([
    "/kaoyan/api/multi-user/me/guardianships",
    "/kaoyan/api/multi-user/learners/learner/guardian-report",
  ]);
  expect(relationship.permissions).toEqual(["assign_materials", "view_reports"]);
});

it("hides the old permission and authorizes only the remaining permissions", async () => {
  render(<GuardianRelationshipsEditor learnerId="learner" learnerUsername="Learner" users={[
    { id: "new-guardian", username: "New guardian", role: "user", preset: "standard" } as UserRecord,
  ]} />);
  await screen.findByText("Existing guardian");
  expect(screen.getByText("2 public books · 1 enabled resources")).toBeVisible();
  expect(screen.queryByText("Manage materials")).not.toBeInTheDocument();
  expect(screen.queryByText("assign_materials")).not.toBeInTheDocument();
  expect(screen.getAllByRole("checkbox")).toHaveLength(3);
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "new-guardian" } });
  fireEvent.click(screen.getByRole("button", { name: "Authorize guardian" }));
  await waitFor(() => expect(apiFetch).toHaveBeenCalledWith("/kaoyan/api/multi-user/guardians", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ guardian_user_id: "new-guardian", learner_user_id: "learner",
      permissions: ["manage_restrictions", "view_reports", "reset_credentials"] }),
  }));
  expect(relationship.permissions).toEqual(["assign_materials", "view_reports"]);
});
