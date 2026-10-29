import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useState } from "react";
import KnowledgeSelector from "@/components/chat/home/KnowledgeSelector";
import KbSettingsSection from "@/components/knowledge/KbSettingsSection";
import KbDocumentList from "@/components/knowledge/KbDocumentList";
import KnowledgeHome from "@/components/knowledge/KnowledgeHome";
import { KbFileDeleteBlockedError } from "@/features/knowledge/api/client";
import { WithdrawalConfirmationError } from "@/lib/withdrawal-confirmation";
import type { KnowledgeBase } from "@/lib/knowledge-helpers";

const fixture = vi.hoisted(() => ({
  auth: { isAdmin: false, isTeacher: true, allowPrivateKb: true },
  references: vi.fn(),
  files: vi.fn(),
  deleteFile: vi.fn(),
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({
  t: (key: string, values?: Record<string, unknown>) =>
    key.replace(/\{\{(\w+)}}/g, (_, name: string) => String(values?.[name] ?? name)),
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
  fixture.references.mockReset().mockResolvedValue({ references: [], other_references: { total: 0, by_kind: {} } });
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

it("lets the owner withdraw despite other accounts' references, shown only as counts (R1)", async () => {
  fixture.references.mockResolvedValue({
    references: [{ referrer_kind: "partner", referrer_id: "mine", created_by: "teacher" }],
    other_references: { total: 3, by_kind: { course: 2, partner: 1 } },
  });
  const change = vi.fn().mockResolvedValue(undefined);
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<KbSettingsSection kb={{ ...owned, visibility: "public" }} onVisibilityChange={change} />);
  expect(await screen.findByText("partner: mine")).toBeTruthy();
  expect(screen.getByText("3 items from other accounts use this material and will show it as unavailable.")).toBeTruthy();
  const button = screen.getByRole("button", { name: "Make private" });
  await waitFor(() => expect(button).not.toBeDisabled());
  fireEvent.click(button);
  await waitFor(() => expect(change).toHaveBeenCalledWith("private"));
  expect(change).toHaveBeenCalledOnce();
  expect(confirm).not.toHaveBeenCalled();
});

it.each([true, false])("asks to confirm teacher-path impact by counts, then retries with confirm (accept=%s)", async (accept) => {
  const change = vi.fn()
    .mockRejectedValueOnce(new WithdrawalConfirmationError("confirm", { affectedPaths: 2, affectedStudents: 5 }))
    .mockResolvedValue(undefined);
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(accept);
  render(<KbSettingsSection kb={{ ...owned, visibility: "public" }} onVisibilityChange={change} />);
  const button = screen.getByRole("button", { name: "Make private" });
  await waitFor(() => expect(button).not.toBeDisabled());
  fireEvent.click(button);
  await waitFor(() => expect(confirm).toHaveBeenCalledOnce());
  expect(confirm.mock.calls[0][0]).toBe(
    "2 published teacher paths use this material\n5 students assigned\nThose paths will show this material as unavailable. Continue?",
  );
  if (accept) await waitFor(() => expect(change).toHaveBeenLastCalledWith("private", {
    confirmed: { affectedPaths: 2, affectedStudents: 5 },
  }));
  await waitFor(() => expect(button).not.toBeDisabled());
  expect(change).toHaveBeenCalledTimes(accept ? 2 : 1);
  expect(screen.queryByRole("alert")).toBeNull();
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

it.each(["account", "inherited_workspace"] as const)(
  "keeps an account alias on A through deselect/reselect in %s",
  (scope) => {
    // Matches the actual /list response asserted by the Python scope test:
    // inherited W also has a user:kb ID; public A is a canonical extra row.
    const account: KnowledgeBase = {
      name: "notes", id: scope === "account" ? "user:kb:notes" : "kb:A",
      kb_id: "A", owned: true, assigned: scope !== "account", read_only: false,
      visibility: "public", legacy_refs: ["account:kb:notes"],
      provenance_label: scope === "account" ? "Created by you" : "Public knowledge base",
    };
    const workspace: KnowledgeBase = {
      name: "notes", id: scope === "account" ? "kb:W" : "user:kb:notes",
      kb_id: "W", owned: true, assigned: scope === "account", read_only: false,
      visibility: scope === "account" ? "public" : "private",
      legacy_refs: ["workspace:W:kb:notes"],
      provenance_label: scope === "account" ? "Public knowledge base" : "Created by you",
    };
    function Picker() {
      const [selected, setSelected] = useState(["account:kb:notes"]);
      return <><KnowledgeSelector embedded knowledgeBases={[workspace, account]}
        selected={selected} onToggle={(ref) => setSelected((old) =>
          old.includes(ref) ? old.filter((value) => value !== ref) : [...old, ref])} />
        <output data-testid="selection">{JSON.stringify(selected)}</output></>;
    }
    render(<Picker />);
    const a = screen.getByRole("button", { name: `notes${account.provenance_label}` });
    const w = screen.getByRole("button", { name: `notes${workspace.provenance_label}` });
    expect(a).toHaveAttribute("aria-pressed", "true");
    expect(w).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(a);
    expect(screen.getByTestId("selection").textContent).toBe("[]");
    expect(a).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(a);
    expect(screen.getByTestId("selection").textContent).toBe('["kb:A"]');
    expect(a).toHaveAttribute("aria-pressed", "true");
    expect(w).toHaveAttribute("aria-pressed", "false");
  },
);

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
  ], { total: 2, by_kind: { course: 2 } }));
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<KbDocumentList kbName="kb:own" collapsed={false} selectedFile={null}
    onSelect={vi.fn()} onToggleCollapsed={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Delete file" }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
  if (code === "requires_confirmation") {
    await waitFor(() => expect(fixture.deleteFile).toHaveBeenCalledWith("kb:own", "sample.txt", { confirm: true }));
    expect(confirm.mock.calls[0][0]).toContain("partner:reader");
    expect(confirm.mock.calls[0][0]).toContain("2 items from other accounts use this material");
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

it("lets an admin only withdraw or delete someone else's published KB (spec 2026-10-29 D10)", async () => {
  fixture.auth = { isAdmin: true, isTeacher: false, allowPrivateKb: true };
  const change = vi.fn().mockResolvedValue(undefined);
  render(<KbSettingsSection
    kb={{ ...owned, owned: false, read_only: true, can_retire: true, visibility: "public" }}
    onSetDefault={vi.fn()} onDelete={vi.fn()} onMove={vi.fn()} onVisibilityChange={change} />);
  expect(screen.queryByRole("button", { name: "Set as default" })).toBeNull();
  expect(screen.getByRole("button", { name: "Delete knowledge base" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Make private" }));
  await waitFor(() => expect(change).toHaveBeenCalledWith("private"));
});
