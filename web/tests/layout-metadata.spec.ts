import { readFileSync } from "node:fs";
import ts from "typescript";
import { afterEach, describe, expect, it, vi } from "vitest";

const originalBasePath = process.env.NEXT_PUBLIC_BASE_PATH;

afterEach(() => {
  if (originalBasePath === undefined) delete process.env.NEXT_PUBLIC_BASE_PATH;
  else process.env.NEXT_PUBLIC_BASE_PATH = originalBasePath;
  vi.resetModules();
});

describe("layout icon metadata", () => {
  it.each(["", "/kaoyan"])("uses deployed asset paths at %j", async (basePath) => {
    process.env.NEXT_PUBLIC_BASE_PATH = basePath;
    vi.resetModules();
    const { assetPath } = await import("@/lib/workspace-scope");
    // Evaluate the real metadata declaration without importing fonts/providers.
    const source = ts.createSourceFile(
      "layout.tsx",
      readFileSync("app/layout.tsx", "utf8"),
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.TSX,
    );
    const declaration = source.statements
      .filter(ts.isVariableStatement)
      .flatMap((statement) => [...statement.declarationList.declarations])
      .find((item) => item.name.getText(source) === "metadata");
    expect(declaration?.initializer).toBeDefined();
    const metadata = new Function(
      "assetPath",
      `return (${declaration!.initializer!.getText(source)});`,
    )(assetPath);
    expect(metadata.title).toBe("DeepTutor");
    expect(metadata.icons).toEqual({
      icon: [
        { url: `${basePath}/favicon-16x16.png`, sizes: "16x16", type: "image/png" },
        { url: `${basePath}/favicon-32x32.png`, sizes: "32x32", type: "image/png" },
      ],
      apple: `${basePath}/apple-touch-icon.png`,
    });
  });
});
