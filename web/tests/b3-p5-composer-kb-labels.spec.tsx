import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { initI18n } from "@/i18n/init";

initI18n("en");

const captured = vi.hoisted(() => ({
  props: null as Record<string, unknown> | null,
}));

vi.mock("@/features/chat/ChatStateAdapter", () => ({
  useChatStateAdapter: () => ({
    state: {
      messages: [],
      knowledgeBases: ["kb:abc"],
      llmSelection: null,
      personaSelection: "",
      resourceSelection: { skills: [], mcp: [] },
      workspaceId: "ws-1",
      isStreaming: false,
    },
    sendMessage: vi.fn(),
    submitUserReply: vi.fn(),
    cancelStreamingTurn: () => undefined,
    setKBs: () => undefined,
    setLLMSelection: () => undefined,
    setPersonaSelection: () => undefined,
    setResourceSelection: () => undefined,
  }),
}));

vi.mock("@/hooks/useWorkspaceChatActions", () => ({
  useWorkspaceChatActions: () => ({
    capabilities: [],
    activeCapabilityValue: "",
    selectCapability: () => undefined,
  }),
}));

vi.mock("@/hooks/useContextBudget", () => ({
  useContextBudget: () => null,
}));

vi.mock("@/hooks/useChatWorkspaces", () => ({
  useChatWorkspaces: () => ({ workspaces: [], error: "" }),
}));

vi.mock("@/hooks/useComposerResources", () => ({
  useComposerResources: () => ({ skills: [], mcp: [] }),
}));

vi.mock("@/components/chat/home/StandaloneComposer", () => ({
  default: (props: Record<string, unknown>) => {
    captured.props = props;
    return <div>composer</div>;
  },
}));

const { MasteryComposer } = await import(
  "@/components/space/learning/MasteryComposer"
);

describe("B3-6a KB display name labels", () => {
  beforeEach(() => {
    captured.props = null;
  });

  it("threads knowledgeBaseLabels through to StandaloneComposer", () => {
    const labels = { "kb:abc": "Linear Algebra" };
    render(
      <MasteryComposer
        placeholder="Ask"
        knowledgeBaseLabels={labels}
      />,
    );
    expect(captured.props).not.toBeNull();
    expect(captured.props?.knowledgeBaseLabels).toEqual(labels);
  });
});
