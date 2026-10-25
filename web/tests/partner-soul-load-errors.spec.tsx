import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import PartnerConfigure from "@/components/partners/PartnerConfigure";
import { apiFetch } from "@/lib/api";
import type { PartnerInfo } from "@/lib/partners-api";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock("@/components/common/MarkdownRenderer", () => ({ default: () => null }));
vi.mock("@/components/partners/FaceEditor", () => ({ default: () => null }));
vi.mock("@/components/partners/PartnerModelSelect", () => ({ default: () => null }));
vi.mock("@/components/partners/PartnerWorkspacePicker", () => ({ default: () => null }));
vi.mock("@/components/partners/ToolPicker", () => ({ default: () => null }));
vi.mock("@/components/partners/AssetPicker", () => ({ default: () => null }));
vi.mock("@/lib/llm-options", () => ({ listLLMOptions: async () => ({ options: [], active: null }) }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, apiUrl: (url: string) => url, apiFetch: vi.fn() };
});

const partner: PartnerInfo = { partner_id: "study", name: "Study", description: "", channels: [], running: false, started_at: null };
afterEach(() => { cleanup(); vi.clearAllMocks(); });

function setup(status: number, content = "Existing persona") {
  vi.mocked(apiFetch).mockImplementation(async (url, options) => {
    if (String(url).endsWith("/soul")) {
      if (options?.method === "PUT") return Response.json({ saved: true });
      return Response.json(status === 200 ? { content } : { detail: "Persona storage unavailable" }, { status });
    }
    if (String(url).endsWith("/tool-options")) return Response.json({ tools: [], builtin_tools: [], mcp_tools: [] });
    return Response.json({ knowledge_bases: [], skills: [], notebooks: [] });
  });
}

function soulSection() {
  return within(screen.getByRole("heading", { name: "Soul" }).closest("section")!);
}

it("does not allow a failed persona read to be saved as an empty document", async () => {
  setup(503);
  render(<PartnerConfigure partner={partner} onToast={vi.fn()} onUpdated={vi.fn()} />);
  await waitFor(() => expect(apiFetch).toHaveBeenCalled());
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  const save = soulSection().getByRole("button", { name: "Save" });
  await act(async () => { fireEvent.click(save); });
  expect(apiFetch).not.toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ method: "PUT" }));
  expect(save).toBeDisabled();
});

it("allows an intentionally empty successfully loaded persona to be saved", async () => {
  setup(200, "");
  render(<PartnerConfigure partner={partner} onToast={vi.fn()} onUpdated={vi.fn()} />);
  const save = soulSection().getByRole("button", { name: "Save" });
  await waitFor(() => expect(save).not.toBeDisabled());
  await act(async () => { fireEvent.click(save); });
  await waitFor(() => expect(apiFetch).toHaveBeenCalledWith("/api/partners/study/soul", expect.objectContaining({ method: "PUT", body: JSON.stringify({ content: "" }) })));
});

it("loads the stored persona on retry before enabling save", async () => {
  setup(503);
  render(<PartnerConfigure partner={partner} onToast={vi.fn()} onUpdated={vi.fn()} />);
  await screen.findByText("Persona storage unavailable");
  setup(200);
  fireEvent.click(soulSection().getByRole("button", { name: "Retry" }));
  await waitFor(() => expect(soulSection().getByRole("textbox")).toHaveValue("Existing persona"));
  expect(soulSection().getByRole("button", { name: "Save" })).not.toBeDisabled();
});

it.each([
  { name: "Adopted copy", ref: "kb:stable-id", adopted: true },
  { name: "Legacy copy", legacy: true },
])("removes $name by its reference or legacy name", async kb => {
  vi.mocked(apiFetch).mockImplementation(async (url, options) => {
    if (String(url).endsWith("/soul")) return Response.json({ content: "" });
    if (String(url).endsWith("/tool-options")) return Response.json({ tools: [], builtin_tools: [], mcp_tools: [] });
    if (options?.method === "DELETE") return Response.json({ assets: { knowledge_bases: [], skills: [], notebooks: [] } });
    return Response.json({ knowledge_bases: [kb], skills: [], notebooks: [] });
  });
  render(<PartnerConfigure partner={partner} onToast={vi.fn()} onUpdated={vi.fn()} />);
  const row = (await screen.findByText(kb.name)).closest("li")!;
  const notice = "The original knowledge base no longer exists. This copy is now your private knowledge base; review it in the Knowledge Center.";
  if ("adopted" in kb) expect(within(row).getByText(notice)).toBeVisible();
  else expect(within(row).queryByText(notice)).not.toBeInTheDocument();
  fireEvent.click(within(row).getByRole("button", { name: "Remove" }));
  await waitFor(() => expect(screen.queryByText(kb.name)).not.toBeInTheDocument());
  expect(apiFetch).toHaveBeenCalledWith(
    expect.stringContaining(encodeURIComponent(kb.ref ?? kb.name)),
    expect.objectContaining({ method: "DELETE" }),
  );
});
