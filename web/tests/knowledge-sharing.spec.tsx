import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useState } from "react";
import KnowledgeSelector from "@/components/chat/home/KnowledgeSelector";
import KbSettingsSection from "@/components/knowledge/KbSettingsSection";
import KbDocumentList from "@/components/knowledge/KbDocumentList";
import KnowledgeHome from "@/components/knowledge/KnowledgeHome";
import { KbFileDeleteBlockedError } from "@/features/knowledge/api/client";
import type { KnowledgeBase } from "@/lib/knowledge-helpers";

const fixture = vi.hoisted(() => ({
  auth: { isAdmin: false, isTeacher: true, allowPrivateKb: true },
  references: vi.fn(),
  files: vi.fn(),
  deleteFile: vi.fn(),
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({
  t: (key: string, values?: Record<string, unknown>) =>
    key.replace(/\{\{(\w+)\}\}/g, (_, name: string) => String(values?.[name] ?? name)),
}) }));
vi.mock("@/hooks/useAuthStatus", () => ({ useAuthStatus: () => fixture.auth }));
vi.mock("@/lib/workspaces-api", () => ({
  listWorkspaces: async () => [], previewKnowledgeMove: vi.fn(),
}));
vi.mock("@/features/knowledge/api/catalog", () => ({
  listKnowledgeBaseReferences: fixture.references,
}));
vi.mock("@/features/knowledge/api/files", async () => ({
  ...(await vi.importActual("@/features/knowledge/api/files")),
  listKnowledgeBaseFiles: fixture.files,
  deleteKbFile: fixture.deleteFile,
}));

const owned: KnowledgeBase = {
  name: "notes", kb_id: "own", id: "account:kb:notes", owned: true,
  read_only: false, can_set_default: true, visibility: "private", created_by: "teacher",
};
beforeEach(() => {
  fixture.auth = { isAdmin: false, isTeacher: true, allowPrivateKb: true };
  fixture.references.mockReset().mockResolvedValue([]);
  fixture.files.mockReset().mockResolvedValue([{ name: "sample.txt", type: "file" }]);
  fixture.deleteFile.mockReset().mockResolvedValue({ was_indexed: false });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it.each([
  ["foreign public", { ...owned, owned: false, read_only: true, visibility: "public" }, true],
  ["existing student owned", { ...owned, is_default: true }, false],
] as const)("hides every settings write action for %s while preserving the default marker", async (_, kb, allowed) => {
  fixture.auth.allowPrivateKb = allowed;
  render(<KbSettingsSection kb={kb} onSetDefault={vi.fn()} onDelete={vi.fn()} onMove={vi.fn()} onVisibilityChange={vi.fn()} />);
  expect(screen.queryByRole("button")).toBeNull();
  expect(screen.queryByText("Currently default") !== null).toBe(Boolean(kb.is_default));
  expect(fixture.references).not.toHaveBeenCalled();
});

it("allows managing an external owned KB but never offers a scoped default", async () => {
  render(<KbSettingsSection kb={{ ...owned, id: "kb:own", can_set_default: false }}
    onSetDefault={vi.fn()} onDelete={vi.fn()} onVisibilityChange={vi.fn()} />);
  expect(screen.queryByRole("button", { name: "Set as default" })).toBeNull();
  expect(await screen.findByRole("button", { name: "Make public" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Delete knowledge base" })).toBeTruthy();
});

it("publishes with a real callback and requires admin confirmation before forced withdrawal", async () => {
  fixture.auth.isAdmin = true;
  fixture.references.mockResolvedValue([{ referrer_kind: "partner", referrer_id: "p", created_by: "other" }]);
  const change = vi.fn().mockResolvedValue(undefined);
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<KbSettingsSection kb={{ ...owned, visibility: "public" }} onVisibilityChange={change} />);
  const button = screen.getByRole("button", { name: "Make private" });
  await waitFor(() => expect(button).not.toBeDisabled());
  fireEvent.click(button);
  await waitFor(() => expect(change).toHaveBeenCalledWith("private", true));
  expect(confirm).toHaveBeenCalledOnce();
});

it("matches and deselects relocated admin aliases without selecting a namesake", () => {
  function Picker() {
    const [selected, setSelected] = useState(["admin:kb:notes"]);
    return <><KnowledgeSelector embedded knowledgeBases={[
      { name: "relocated", kb_id: "moved", id: "kb:moved", legacy_refs: ["admin:kb:notes"], owned: false },
      { name: "notes", kb_id: "other", id: "kb:other", owned: false },
    ]} selected={selected} onToggle={(ref) => setSelected((old) => old.includes(ref) ? old.filter((v) => v !== ref) : [...old, ref])} />
    <output data-testid="selection">{JSON.stringify(selected)}</output></>;
  }
  render(<Picker />);
  fireEvent.click(screen.getByRole("button", { name: /relocated/ }));
  expect(screen.getByTestId("selection").textContent).toBe("[]");
  fireEvent.click(screen.getByRole("button", { name: /relocated/ }));
  expect(screen.getByTestId("selection").textContent).toBe('["kb:moved"]');
});

it.each([true, false])("preserves files without delete, move or folder controls (readOnly=%s)", async (readOnly) => {
  fixture.auth.allowPrivateKb = readOnly;
  render(<KbDocumentList kbName="kb:own" readOnly={readOnly} collapsed={false}
    selectedFile={null} onSelect={vi.fn()} onToggleCollapsed={vi.fn()} />);
  expect(await screen.findByRole("button", { name: "sample.txt" })).toBeTruthy();
  for (const name of ["Delete file", "Move to…", "New folder"]) expect(screen.queryByRole("button", { name })).toBeNull();
});

it.each(["requires_confirmation", "document_referenced"] as const)("handles document deletion guard %s", async (code) => {
  fixture.deleteFile.mockRejectedValueOnce(new KbFileDeleteBlockedError("Protected", code, [
    { kb_id: "own", referrer_kind: "partner", referrer_id: "reader", created_by: "teacher", doc_ids: null },
  ]));
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<KbDocumentList kbName="kb:own" collapsed={false} selectedFile={null}
    onSelect={vi.fn()} onToggleCollapsed={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Delete file" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
  if (code === "requires_confirmation") {
    await waitFor(() => expect(fixture.deleteFile).toHaveBeenCalledWith("kb:own", "sample.txt", { confirm: true }));
    expect(confirm.mock.calls[0][0]).toContain("partner:reader");
  } else {
    expect(await screen.findByText(/Protected/)).toBeTruthy();
    expect(fixture.deleteFile).toHaveBeenCalledTimes(1);
    expect(confirm).not.toHaveBeenCalled();
  }
});

it("requires explicit admin consent before force deleting a pinned document", async () => {
  fixture.auth.isAdmin = true;
  fixture.deleteFile.mockRejectedValueOnce(new KbFileDeleteBlockedError("Pinned", "document_referenced", []));
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<KbDocumentList kbName="kb:own" collapsed={false} selectedFile={null}
    onSelect={vi.fn()} onToggleCollapsed={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Delete file" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
  await waitFor(() => expect(fixture.deleteFile).toHaveBeenLastCalledWith("kb:own", "sample.txt", { force: true }));
});

it("groups public/private/connectors and removes creation and external connection entry points", () => {
  const common = { providers: [], onOpenKb: vi.fn(), onSectionChange: vi.fn() };
  const { rerender } = render(<KnowledgeHome {...common} activeSection="knowledge-bases" kbs={[
    owned, { name: "atlas", visibility: "public", owned: false, created_by_name: "teacher" },
    { name: "vault", connector: true },
  ]} />);
  for (const name of ["Public knowledge bases", "My private knowledge bases", "My connectors"]) {
    expect(screen.getByRole("heading", { name: new RegExp(name) })).toBeTruthy();
  }
  expect(screen.getByText("Shared by teacher")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "New knowledge base" })).toBeNull();
  rerender(<KnowledgeHome {...common} activeSection="knowledge-engines" kbs={[]} />);
  expect(screen.queryByText("External knowledge sources")).toBeNull();
});
