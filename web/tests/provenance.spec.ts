import { expect, it } from "vitest";

import { groupByProvenance, provenanceGroupTitle, provenanceLabel } from "@/lib/provenance";

const t = (key: string, values?: Record<string, unknown>) =>
  key.replace(/\{\{(\w+)\}\}/g, (_: string, name: string) => String(values?.[name] ?? ""));

it("groups what was handed out before one's own, leaving out empty groups", () => {
  const items = [
    { id: "a", provenance: { kind: "own" as const, name: "" } },
    { id: "b", provenance: { kind: "teacher" as const, name: "tina" } },
    { id: "c", provenance: undefined },
    { id: "d", provenance: { kind: "school" as const, name: "" } },
  ];
  const groups = groupByProvenance(items, (item) => item.provenance);
  expect(groups.map((g) => [g.kind, g.items.map((i) => i.id)])).toEqual([
    ["teacher", ["b"]],
    ["school", ["d"]],
    ["own", ["a", "c"]],
  ]);
});

it("labels a teacher's item as assigned for students and shared for teachers", () => {
  const tina = { kind: "teacher" as const, name: "tina" };
  expect(provenanceLabel(tina, t, true)).toBe("Assigned by tina");
  expect(provenanceLabel(tina, t, false)).toBe("Shared by tina");
  expect(provenanceLabel({ kind: "school", name: "" }, t, true)).toBe("Published by the platform");
  expect(provenanceLabel(undefined, t, true)).toBe("Mine");
  expect(provenanceGroupTitle("teacher", t, true)).toBe("From my teachers");
  expect(provenanceGroupTitle("teacher", t, false)).toBe("From other teachers");
});
