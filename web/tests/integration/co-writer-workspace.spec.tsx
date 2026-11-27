import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const source = (relative: string) =>
  fs.readFileSync(path.resolve(process.cwd(), relative), "utf8");

describe("co-writer workspace boundaries", () => {
  it("keeps the dynamic route as composition only", () => {
    const route = source("app/(workspace)/co-writer/[docId]/page.tsx");
    expect(route).toMatch(/<NoteWorkspace key=/);
    expect(route).not.toMatch(/apiFetch|localStorage|useState|useEffect/);
    expect(route.split("\n").length).toBeLessThan(35);
  });

  it("uses the shared block notebook and retains legacy draft recovery", () => {
    const workspace = source(
      "features/notes/components/NoteWorkspace.tsx",
    );
    expect(workspace).toMatch(/loadDraft/);
    expect(workspace).toMatch(/expected_revision/);
    expect(workspace).toMatch(/RequireCapability capability="llm"/);
    expect(fs.existsSync(path.resolve(process.cwd(), "features/co-writer/components/CoWriterWorkspace.tsx"))).toBe(false);
    expect(workspace).not.toMatch(
      /\.localStorage\.(?:getItem|setItem|removeItem)/,
    );
  });
});
