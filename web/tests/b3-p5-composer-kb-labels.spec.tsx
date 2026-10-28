/**
 * B3-6a rendered test: when the KB catalog is empty but knowledgeBaseLabels
 * provides a mapping, the selected-resource chip displays the label name
 * ("Linear Algebra"), NOT the raw ref ("kb:abc").
 *
 * This renders SelectedResources with items built using the exact same
 * label-resolution expression from ChatComposer.tsx line ~800:
 *
 *   knowledgeBases.find(kb => knowledgeBaseMatches(kb, id))?.name
 *     || knowledgeBaseLabels?.[id]
 *     || id
 *
 * Red proof: removing `knowledgeBaseLabels?.[id]` makes the label fall
 * through to the raw id, failing the "Linear Algebra" assertion.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Database } from "lucide-react";

import { initI18n } from "@/i18n/init";
import type { ContextTreeItem } from "@/components/chat/home/ContextReferenceTree";
import { knowledgeBaseMatches } from "@/lib/knowledge-helpers";
import type { KnowledgeBaseSummary } from "@/features/knowledge/api/catalog";

initI18n("en");

// Mock framer-motion to avoid animation-related test issues.
vi.mock("framer-motion", () => ({
  AnimatePresence: ({ children }: { children: React.ReactNode }) => children,
  m: {
    div: ({
      children,
      ...props
    }: React.HTMLAttributes<HTMLDivElement> & {
      layout?: string;
      initial?: unknown;
      animate?: unknown;
      exit?: unknown;
      transition?: unknown;
    }) => <div {...props}>{children}</div>,
  },
  useReducedMotion: () => true,
}));

// ResourceReuse context is optional; SelectedResources calls useResourceReuse().
vi.mock("@/components/chat/home/ResourceReuse", () => ({
  useResourceReuse: () => null,
  ResourceReuseContext: { Provider: ({ children }: { children: React.ReactNode }) => children },
  ResourceReuseControl: () => null,
}));

vi.mock("@/shared/ui/Tooltip", () => ({
  default: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

// Import SelectedResources after mocks are set up.
const { default: SelectedResources } = await import(
  "@/components/chat/home/SelectedResources"
);

/**
 * Build contextTreeItems the same way ChatComposer does (line ~800).
 * This is the exact production expression — any drift is intentional
 * and should be caught by code review.
 */
function buildKbItems(
  selectedKnowledgeBases: string[],
  knowledgeBases: KnowledgeBaseSummary[],
  knowledgeBaseLabels?: Record<string, string>,
): ContextTreeItem[] {
  return selectedKnowledgeBases.map(
    (id): ContextTreeItem => ({
      key: `kb-${id}`,
      icon: Database,
      kind: "Knowledge",
      label:
        knowledgeBases.find((kb) => knowledgeBaseMatches(kb, id))?.name ||
        knowledgeBaseLabels?.[id] ||
        id,
      onRemove: () => undefined,
    }),
  );
}

describe("B3-6a KB display name labels (rendered)", () => {
  it("displays the label from knowledgeBaseLabels when the KB catalog is empty", () => {
    const items = buildKbItems(
      ["kb:abc"],
      [], // empty catalog — the student cannot see this referenced KB
      { "kb:abc": "Linear Algebra" },
    );

    render(<SelectedResources items={items} />);

    // The chip must show the friendly name.
    expect(screen.getByText("Linear Algebra")).toBeTruthy();
    // The raw ref must NOT appear as visible text anywhere.
    expect(screen.queryByText("kb:abc")).toBeNull();
  });

  it("prefers the catalog name over the label fallback", () => {
    const catalogKb: KnowledgeBaseSummary = {
      name: "Catalog Name",
      id: "kb:abc",
      statistics: {},
    };
    const items = buildKbItems(
      ["kb:abc"],
      [catalogKb],
      { "kb:abc": "Label Fallback" },
    );

    render(<SelectedResources items={items} />);

    expect(screen.getByText("Catalog Name")).toBeTruthy();
    expect(screen.queryByText("Label Fallback")).toBeNull();
  });

  it("falls back to the raw id when both catalog and labels miss", () => {
    const items = buildKbItems(["kb:xyz"], [], undefined);

    render(<SelectedResources items={items} />);

    expect(screen.getByText("kb:xyz")).toBeTruthy();
  });
});
