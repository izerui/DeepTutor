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
  tools: [{ name: "reason" }], mcp_tools: [],
};
const stored: GrantPayload = {
  version: 2, user_id: "student", models: { llm: [] },
  knowledge_bases: [{ resource_id: "old", name: "Retired KB", custom: { preserve: [1, "two"] } }],
  skills: [], partners: [], enabled_tools: null, mcp_tools: null, exec_enabled: null,
  ai_daily_tokens: null,
};

it("saves other assignments and the daily AI limit without altering stored KB grants", async () => {
  vi.mocked(fetchAdminResources).mockResolvedValue(resources);
  vi.mocked(fetchUserGrant).mockResolvedValue(structuredClone(stored));
  vi.mocked(saveUserGrant).mockImplementation(async (_id, grant) => grant);
  render(<GrantEditor userId="student" />);
  await screen.findByRole("heading", { name: "Daily AI limit" });
  // Spec 2026-11-05: no feature switches are offered any more.
  expect(screen.queryByRole("combobox", { name: "Create their own knowledge bases" })).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Knowledge" })).not.toBeInTheDocument();
  expect(screen.queryByText(/KBs/)).not.toBeInTheDocument();
  expect(screen.queryByText("Retired KB")).not.toBeInTheDocument();
  // Spec 2026-10-29 D5: partners are not lent through grants, so there is no partner checkbox.
  expect(screen.queryByRole("checkbox", { name: /^Partner One/ })).not.toBeInTheDocument();
  // Spec 2026-11-06: learning restrictions are gone from the grant editor.
  expect(screen.queryByText("Learning policy")).not.toBeInTheDocument();
  expect(screen.queryByRole("checkbox", { name: /Enable learning policy/ })).not.toBeInTheDocument();
  for (const name of ["Model One", "Skill One"])
    fireEvent.click(screen.getByRole("checkbox", { name: new RegExp(`^${name}`) }));
  fireEvent.change(screen.getByRole("combobox", { name: "Daily AI limit" }), { target: { value: "200000" } });
  fireEvent.click(screen.getByRole("button", { name: "Save assignments" }));
  await waitFor(() => expect(saveUserGrant).toHaveBeenCalledOnce());
  const saved = vi.mocked(saveUserGrant).mock.calls[0][1];
  expect(saved.knowledge_bases).toEqual(stored.knowledge_bases);
  expect(saved.models.llm).toEqual([{ profile_id: "model", model_ids: ["one"], source: "admin" }]);
  expect(saved.skills).toEqual([{ skill_id: "Skill One", access: "use", source: "admin" }]);
  expect(saved.partners).toEqual([]);
  expect(saved).not.toHaveProperty("learning_policy");
  expect(saved.ai_daily_tokens).toBe(200000);
  expect(saved).not.toHaveProperty("student_policy");
  expect(saved.enabled_tools).toBeNull();
  expect(saved.mcp_tools).toBeNull();
});
