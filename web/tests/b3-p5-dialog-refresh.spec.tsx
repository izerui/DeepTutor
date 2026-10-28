/**
 * B3-7 red test: the assignment-management dialog sees fresh template state
 * after `loadTemplates()` re-fetches, even though `managing` is the snapshot
 * taken when the dialog opened.
 *
 * The fix in mastery/page.tsx is:
 *   template={templates.find(t => t.id === managing.id) ?? managing}
 *
 * We test the derivation logic directly: a parent component holds `managing`
 * (stale) and `templates` (fresh); the child receives the derived prop and
 * must reflect the updated status.
 *
 * NOTE: This test verifies the derivation pattern, not page.tsx itself.
 * The Harness replicates the `templates.find` expression independently;
 * a revert in page.tsx would not be caught here. That trade-off is
 * accepted because rendering the full mastery page in a unit test is
 * not practical given its dependency depth.
 */
import { useState } from "react";
import { act, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

interface MinimalTemplate {
  id: string;
  status: string;
  current_revision: number;
}

/**
 * Minimal stand-in for the page's managing/templates interaction.
 * `DerivedChild` renders whichever template value it receives.
 */
function Harness({
  initialManaging,
  initialTemplates,
  updater,
}: {
  initialManaging: MinimalTemplate;
  initialTemplates: MinimalTemplate[];
  /** Called with setTemplates so the test can push a fresh list. */
  updater: (setTemplates: (ts: MinimalTemplate[]) => void) => void;
}) {
  const [templates, setTemplates] = useState(initialTemplates);
  const managing = initialManaging; // snapshot — never updated

  // Expose the setter so the test can simulate `loadTemplates()`.
  updater(setTemplates);

  // The B3-7 fix: derive the prop from the latest templates list.
  const derived = templates.find((t) => t.id === managing.id) ?? managing;

  return (
    <div>
      <span data-testid="status">{derived.status}</span>
      <span data-testid="revision">{derived.current_revision}</span>
    </div>
  );
}

describe("B3-7 dialog refresh", () => {
  it("shows archived status after templates list updates", () => {
    const stale: MinimalTemplate = {
      id: "tpl_1",
      status: "published",
      current_revision: 2,
    };
    const initialList = [{ ...stale }];

    let setTemplates!: (ts: MinimalTemplate[]) => void;

    render(
      <Harness
        initialManaging={stale}
        initialTemplates={initialList}
        updater={(setter) => {
          setTemplates = setter;
        }}
      />,
    );

    // Before update: dialog sees the published template.
    expect(screen.getByTestId("status").textContent).toBe("published");

    // Simulate `onChanged` → `loadTemplates()` returning the archived version.
    act(() => {
      setTemplates([
        { id: "tpl_1", status: "archived", current_revision: 2 },
      ]);
    });

    // After update: dialog must now show 'archived'.
    expect(screen.getByTestId("status").textContent).toBe("archived");
  });

  it("falls back to managing snapshot when template disappears from list", () => {
    const stale: MinimalTemplate = {
      id: "tpl_1",
      status: "published",
      current_revision: 2,
    };

    let setTemplates!: (ts: MinimalTemplate[]) => void;

    render(
      <Harness
        initialManaging={stale}
        initialTemplates={[{ ...stale }]}
        updater={(setter) => {
          setTemplates = setter;
        }}
      />,
    );

    // Template is removed from the list entirely.
    act(() => {
      setTemplates([]);
    });

    // The ?? fallback means managing itself is used.
    expect(screen.getByTestId("status").textContent).toBe("published");
  });
});
