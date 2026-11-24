import { useRef, useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MessageSquare } from "lucide-react";

import ChatComposer from "@/components/chat/home/ChatComposer";
import type { CapabilityDef } from "@/features/capabilities/presentation";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock("@/lib/workspace-drafts", () => ({
  currentWorkspaceDraftLocation: () => ({ workspaceId: "", pathname: "/chat" }),
  readWorkspaceDraft: vi.fn().mockResolvedValue(null),
  saveWorkspaceDraft: vi.fn().mockResolvedValue(undefined),
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const activeCap: CapabilityDef = {
  value: "",
  label: "Chat",
  description: "Flexible conversation with any tool",
  icon: MessageSquare,
  allowedTools: [],
};

function Harness({
  createTarget = null,
  onCreateTargetChange,
  onSelectCapability = () => {},
}: {
  createTarget?: string | null;
  onCreateTargetChange?: (value: string | null) => void;
  onSelectCapability?: (value: string) => void;
}) {
  const knowledgeBases: { name: string }[] = [];
  const selectedKnowledgeBases: string[] = [];
  const [spaceOpen, setSpaceOpen] = useState(false);
  const composerRef = useRef<HTMLDivElement>(null);
  const capMenuRef = useRef<HTMLDivElement>(null);
  const capBtnRef = useRef<HTMLButtonElement>(null);
  const spaceMenuRef = useRef<HTMLDivElement>(null);
  const spaceBtnRef = useRef<HTMLButtonElement>(null);
  const dragCounter = useRef(0);

  return (
    <ChatComposer
      composerRef={composerRef}
      capMenuRef={capMenuRef}
      capBtnRef={capBtnRef}
      spaceMenuRef={spaceMenuRef}
      spaceBtnRef={spaceBtnRef}
      dragCounter={dragCounter}
      dragging={false}
      capMenuOpen={false}
      spaceMenuOpen={spaceOpen}
      hasMessages={false}
      attachments={[]}
      attachmentError={null}
      activeCap={activeCap}
      knowledgeBases={knowledgeBases}
      llmOptions={[]}
      activeLLMDefault={null}
      llmSelection={null}
      llmOptionsLoading={false}
      llmOptionsError={false}
      selectedNotebookRecords={[]}
      selectedBookReferences={[]}
      selectedHistorySessions={[]}
      selectedAgentSessions={[]}
      selectedQuestionEntries={[]}
      notebookReferenceGroups={[]}
      selectedPersona={null}
      selectedMemoryFiles={[]}
      selectedKnowledgeBases={selectedKnowledgeBases}
      isStreaming={false}
      isVisualizeMode={false}
      capabilityNeedsConfig={false}
      capabilityConfigConfirmed={false}
      onRequestConfigConfirm={() => {}}
      capabilities={[activeCap]}
      onSetCapMenuOpen={() => {}}
      onSetSpaceMenuOpen={setSpaceOpen}
      onToggleKB={() => {}}
      onSelectLLM={() => {}}
      onSelectNotebookPicker={() => {}}
      onSelectBookPicker={() => {}}
      onSelectHistoryPicker={() => {}}
      onSelectAgentsPicker={() => {}}
      onSelectQuestionBankPicker={() => {}}
      onSelectPersonaPicker={() => {}}
      onSelectMemoryPicker={() => {}}
      onClearPersona={() => {}}
      onToggleMemoryFile={() => {}}
      onSend={() => {}}
      onRemoveAttachment={() => {}}
      onRemoveHistory={() => {}}
      onRemoveAgent={() => {}}
      onRemoveBookReference={() => {}}
      onRemoveNotebook={() => {}}
      onRemoveQuestion={() => {}}
      onDragEnter={() => {}}
      onDragLeave={() => {}}
      onDragOver={() => {}}
      onDrop={() => {}}
      onPaste={() => {}}
      onAddFiles={() => {}}
      onSelectCapability={onSelectCapability}
      createTarget={createTarget}
      onCreateTargetChange={onCreateTargetChange}
      onCancelStreaming={() => {}}
    />
  );
}


it("offers Skill and Persona under Create and switches the turn back to plain chat", () => {
  const onCreateTargetChange = vi.fn();
  const onSelectCapability = vi.fn();
  render(
    <Harness onCreateTargetChange={onCreateTargetChange} onSelectCapability={onSelectCapability} />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Resources" }));
  fireEvent.click(screen.getByRole("button", { name: /^Create/ }));
  expect(screen.getByText("composer.tray.createHintSkill")).toBeInTheDocument();
  expect(screen.getByText("composer.tray.createHintPersona")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /Skill.*composer\.tray\.createHintSkill/ }));
  expect(onCreateTargetChange).toHaveBeenCalledWith("skill");
  expect(onSelectCapability).toHaveBeenCalledWith("");
});

it("shows the active kind as a removable chip and a create placeholder", () => {
  const onCreateTargetChange = vi.fn();
  render(<Harness createTarget="persona" onCreateTargetChange={onCreateTargetChange} />);
  const chip = screen.getByTestId("composer-create-chip");
  expect(chip).toHaveTextContent("Create");
  expect(chip).toHaveTextContent("Persona");
  expect(screen.getByRole("textbox")).toHaveAttribute(
    "placeholder",
    "composer.create.placeholder.persona",
  );
  fireEvent.click(screen.getByRole("button", { name: "composer.create.leave" }));
  expect(onCreateTargetChange).toHaveBeenCalledWith(null);
});

it("picking the active kind again leaves create mode", () => {
  const onCreateTargetChange = vi.fn();
  render(<Harness createTarget="skill" onCreateTargetChange={onCreateTargetChange} />);
  fireEvent.click(screen.getByRole("button", { name: "Resources" }));
  fireEvent.click(screen.getByRole("button", { name: /^Create/ }));
  fireEvent.click(screen.getByRole("button", { name: /Skill.*composer\.tray\.createHintSkill/ }));
  expect(onCreateTargetChange).toHaveBeenCalledWith(null);
});

it("has no Create entry when the surface does not support create mode", () => {
  render(<Harness />);
  fireEvent.click(screen.getByRole("button", { name: "Resources" }));
  expect(screen.queryByRole("button", { name: /^Create/ })).not.toBeInTheDocument();
  expect(screen.queryByTestId("composer-create-chip")).not.toBeInTheDocument();
});
