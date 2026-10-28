/**
 * B3-7 test: the `currentTemplate` PRODUCT function in mastery-templates.ts
 * resolves the freshest version of a template from the live list.
 *
 * Red proof: making `currentTemplate` always return `managing` (ignoring the
 * list) causes the "picks the updated template" test to fail.
 */
import { describe, expect, it } from "vitest";

import { currentTemplate } from "@/lib/mastery-templates";

interface MinimalTemplate {
  id: string;
  status: string;
  current_revision: number;
}

describe("currentTemplate (product function)", () => {
  it("picks the updated template from the list over the stale snapshot", () => {
    const stale: MinimalTemplate = {
      id: "tpl_1",
      status: "published",
      current_revision: 2,
    };
    const fresh: MinimalTemplate = {
      id: "tpl_1",
      status: "archived",
      current_revision: 2,
    };

    const result = currentTemplate([fresh], stale);

    expect(result.status).toBe("archived");
    expect(result).toBe(fresh); // identity: the list entry, not the snapshot
  });

  it("falls back to the managing snapshot when the template is not in the list", () => {
    const stale: MinimalTemplate = {
      id: "tpl_1",
      status: "published",
      current_revision: 2,
    };

    const result = currentTemplate([], stale);

    expect(result).toBe(stale);
    expect(result.status).toBe("published");
  });

  it("returns the matching entry even when the list has multiple templates", () => {
    const stale: MinimalTemplate = {
      id: "tpl_2",
      status: "draft",
      current_revision: 0,
    };
    const other: MinimalTemplate = {
      id: "tpl_1",
      status: "published",
      current_revision: 3,
    };
    const updated: MinimalTemplate = {
      id: "tpl_2",
      status: "published",
      current_revision: 1,
    };

    const result = currentTemplate([other, updated], stale);

    expect(result).toBe(updated);
    expect(result.status).toBe("published");
    expect(result.current_revision).toBe(1);
  });
});
