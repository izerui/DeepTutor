import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import EngineDetail from "@/features/knowledge/components/engines/EngineDetail";

const auth = vi.hoisted(() => ({ isAdmin: false, enabled: true }));
const calls = vi.hoisted(() => ({ ima: vi.fn(), llama: vi.fn(), models: vi.fn() }));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock("@/hooks/useAuthStatus", () => ({
  useAuthStatus: () => ({
    statusAvailable: true,
    enabled: auth.enabled,
    isAdmin: auth.isAdmin,
    authenticated: true,
    loading: false,
    userId: "u",
  }),
}));
vi.mock("@/features/knowledge/api/engines", async (original) => ({
  ...(await original<typeof import("@/features/knowledge/api/engines")>()),
  getEnginePreflight: async () => ({ ok: true, checks: [] }),
  getImaConfig: async () => {
    calls.ima();
    return { client_id: "", api_key_set: false, configured: false };
  },
  getLlamaIndexConfig: async () => {
    calls.llama();
    return { retrieval_profile: "vector", top_k: 5, effective_retrieval_profile: "vector" };
  },
  getEngineModelOptions: async () => {
    calls.models();
    return { embedding: { active: { profile_id: null, model_id: null }, options: [] } };
  },
}));

beforeEach(() => {
  calls.ima.mockClear();
  calls.llama.mockClear();
  calls.models.mockClear();
});

const renderEngine = (id: string) =>
  render(
    <EngineDetail
      provider={{ id, name: id, description: "" } as never}
      kbs={[]}
      onBack={() => {}}
      onOpenKb={() => {}}
      onSelectMode={() => {}}
      onChanged={() => {}}
      onError={() => {}}
    />,
  );

it("does not offer a learner the deployment's engine settings", async () => {
  auth.isAdmin = false;
  renderEngine("ima");
  expect(screen.getByText("Only administrators can change engine settings.")).toBeTruthy();
  expect(screen.queryByText("Credentials")).toBeNull();
  renderEngine("llamaindex");
  expect(screen.queryByText("Retrieval & chunking")).toBeNull();
  expect(screen.queryByText("Models")).toBeNull();
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(calls.ima).not.toHaveBeenCalled();
  expect(calls.llama).not.toHaveBeenCalled();
  expect(calls.models).not.toHaveBeenCalled();
});

it("keeps the engine settings for an administrator", async () => {
  auth.isAdmin = true;
  renderEngine("ima");
  expect(screen.getByText("Credentials")).toBeTruthy();
  expect(screen.queryByText("Only administrators can change engine settings.")).toBeNull();
  await waitFor(() => expect(calls.ima).toHaveBeenCalled());
});
