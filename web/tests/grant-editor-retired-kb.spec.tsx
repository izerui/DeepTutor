import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { GrantEditor } from "@/features/multi-user/components/GrantEditor";
import { fetchAdminResources, fetchUserGrant, saveUserGrant } from "@/features/multi-user/api";
import type { GrantPayload, MultiUserResources } from "@/features/multi-user/types";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t }) }));
const t = (key: string) => key;
vi.mock("@/features/multi-user/api", () => ({
  fetchAdminResources: vi.fn(), fetchUserGrant: vi.fn(), saveUserGrant: vi.fn(),
}));
afterEach(cleanup);

const resources: MultiUserResources = {
  models: { llm: [{ profile_id: "model", name: "Profile", models: [{ model_id: "one", name: "Model One" }] }] },
  skills: [{ name: "Skill One" }], partners: [{ partner_id: "partner", name: "Partner One" }],
  reading_materials: [], reading_extensions: [], tools: [{ name: "reason" }], mcp_tools: [],
};
const stored: GrantPayload = {
  version: 2, user_id: "student", models: { llm: [] },
  knowledge_bases: [{ resource_id: "old", name: "Retired KB", custom: { preserve: [1, "two"] } }],
  skills: [], partners: [], enabled_tools: null, mcp_tools: null, exec_enabled: null,
  learning_policy: null, student_policy: null,
};

it("saves other assignments and student overrides without altering stored KB grants", async () => {
  vi.mocked(fetchAdminResources).mockResolvedValue(resources);
  vi.mocked(fetchUserGrant).mockResolvedValue(structuredClone(stored));
  vi.mocked(saveUserGrant).mockImplementation(async (_id, grant) => grant);
  render(<GrantEditor userId="student" isStudent />);
  await screen.findByRole("heading", { name: "Student permissions" });
  expect(screen.queryByRole("heading", { name: "Knowledge" })).not.toBeInTheDocument();
  expect(screen.queryByText(/KBs/)).not.toBeInTheDocument();
  expect(screen.queryByText("Retired KB")).not.toBeInTheDocument();
  for (const name of ["Model One", "Skill One", "Partner One", "Enable learning policy", "Allow private knowledge bases"])
    fireEvent.click(screen.getByRole("checkbox", { name: new RegExp(`^${name}`) }));
  fireEvent.click(screen.getByRole("button", { name: "Save assignments" }));
  await waitFor(() => expect(saveUserGrant).toHaveBeenCalledOnce());
  const saved = vi.mocked(saveUserGrant).mock.calls[0][1];
  expect(saved.knowledge_bases).toEqual(stored.knowledge_bases);
  expect(saved.models.llm).toEqual([{ profile_id: "model", model_ids: ["one"], source: "admin" }]);
  expect(saved.skills).toEqual([{ skill_id: "Skill One", access: "use", source: "admin" }]);
  expect(saved.partners).toEqual([{ partner_id: "partner", name: "Partner One", source: "admin" }]);
  expect(saved.learning_policy?.locked_persona).toBe("teacher");
  expect(saved.student_policy).toEqual({ allow_personal_mastery: true, allow_private_kb: true });
  expect(saved.enabled_tools).toBeNull();
  expect(saved.mcp_tools).toBeNull();
});

it("does not show student-only controls for an ordinary user", async () => {
  vi.mocked(fetchAdminResources).mockResolvedValue(resources);
  vi.mocked(fetchUserGrant).mockResolvedValue(structuredClone(stored));
  render(<GrantEditor userId="user" />);
  await screen.findByRole("heading", { name: "Assign access" });
  expect(screen.queryByRole("heading", { name: "Student permissions" })).not.toBeInTheDocument();
});
