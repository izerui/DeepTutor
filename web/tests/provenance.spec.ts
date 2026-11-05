import { expect, it } from "vitest";

import { groupByProvenance, provenanceGroupTitle, provenanceLabel } from "@/lib/provenance";

const t = (key: string, values?: Record<string, unknown>) =>
  key.replace(/\{\{(\w+)\}\}/g, (_: string, name: string) => String(values?.[name] ?? ""));

it("groups what was handed out before one's own, leaving out empty groups", () => {
  const items = [
    { id: "a", provenance: { kind: "own" as const, name: "" } },
    { id: "b", provenance: { kind: "shared" as const, name: "tina" } },
    { id: "c", provenance: undefined },
    { id: "d", provenance: { kind: "school" as const, name: "" } },
  ];
  const groups = groupByProvenance(items, (item) => item.provenance);
  expect(groups.map((g) => [g.kind, g.items.map((i) => i.id)])).toEqual([
    ["shared", ["b"]],
    ["school", ["d"]],
    ["own", ["a", "c"]],
  ]);
});

it("labels another author's item as published by them, the same for everyone", () => {
  expect(provenanceLabel({ kind: "shared", name: "tina" }, t)).toBe("Published by tina");
  expect(provenanceLabel({ kind: "school", name: "" }, t)).toBe("Published by the platform");
  expect(provenanceLabel(undefined, t)).toBe("Mine");
  expect(provenanceGroupTitle("shared", t)).toBe("From other authors");
});
