/**
 * B3-6a tests:
 *
 * 1. Unit tests for the PRODUCT function `resolveKnowledgeBaseLabel` in
 *    `lib/knowledge-helpers.ts` — the same function ChatComposer calls.
 *    Red proof: removing the `knowledgeBaseLabels?.[id]` fallback from the
 *    product function makes the first test fail.
 *
 * 2. Prop-threading test: MasteryComposer passes `knowledgeBaseLabels` through
 *    to StandaloneComposer (verifies the wiring, not the resolution).
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { initI18n } from "@/i18n/init";
import {
  resolveKnowledgeBaseLabel,
  type KnowledgeBase,
} from "@/lib/knowledge-helpers";

initI18n("en");

// ── 1. Product function tests ───────────────────────────────────────────────

describe("resolveKnowledgeBaseLabel (product function)", () => {
  it("returns the label when the KB catalog is empty", () => {
    const result = resolveKnowledgeBaseLabel(
      "kb:abc",
      [], // empty catalog
      { "kb:abc": "Linear Algebra" },
    );
    expect(result).toBe("Linear Algebra");
  });

  it("prefers the catalog name over the label fallback", () => {
    const catalogKb: KnowledgeBase = {
      name: "Catalog Name",
      id: "kb:abc",
    };
    const result = resolveKnowledgeBaseLabel(
      "kb:abc",
      [catalogKb],
      { "kb:abc": "Label Fallback" },
    );
    expect(result).toBe("Catalog Name");
  });

  it("falls back to the raw id when both catalog and labels miss", () => {
    const result = resolveKnowledgeBaseLabel("kb:xyz", [], undefined);
    expect(result).toBe("kb:xyz");
  });

  it("falls back to the raw id when labels is an empty object", () => {
    const result = resolveKnowledgeBaseLabel("kb:xyz", [], {});
    expect(result).toBe("kb:xyz");
  });
});

// ── 2. Prop-threading: MasteryComposer → StandaloneComposer ──────────────

// Capture StandaloneComposer props to verify MasteryComposer threads them.
let captured: { props?: Record<string, unknown> } = {};

vi.mock("@/components/chat/home/StandaloneComposer", () => ({
  __esModule: true,
  default: (props: Record<string, unknown>) => {
    captured.props = props;
    return <div data-testid="standalone-composer" />;
  },
}));
vi.mock("@/features/chat/ChatStateAdapter", () => ({
  useChatStateAdapter: () => ({
    state: { messages: [], knowledgeBases: [], isStreaming: false },
    sendMessage: () => {},
    submitUserReply: () => Promise.resolve(false),
    cancelStreamingTurn: () => {},
    setKBs: () => {},
    setLLMSelection: () => {},
    setPersonaSelection: () => {},
    setResourceSelection: () => {},
  }),
}));
vi.mock("@/hooks/useChatWorkspaces", () => ({
  useChatWorkspaces: () => ({ workspaces: [] }),
}));
vi.mock("@/hooks/useComposerResources", () => ({
  useComposerResources: () => undefined,
}));
vi.mock("@/hooks/useWorkspaceChatActions", () => ({
  useWorkspaceChatActions: () => {},
}));
vi.mock("@/hooks/useContextBudget", () => ({
  useContextBudget: () => null,
}));

const { MasteryComposer } = await import(
  "@/components/space/learning/MasteryComposer"
);

describe("MasteryComposer → StandaloneComposer prop threading", () => {
  it("passes knowledgeBaseLabels to StandaloneComposer", () => {
    captured = {};
    const labels = { "kb:abc": "Linear Algebra" };
    render(
      <MasteryComposer
        placeholder="Ask…"
        knowledgeBaseLabels={labels}
      />,
    );
    expect(captured.props).toBeDefined();
    expect(captured.props?.knowledgeBaseLabels).toEqual(labels);
  });
});
