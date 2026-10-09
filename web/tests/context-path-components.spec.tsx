import { render } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

const ORIGINAL_BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH;

describe("context-path /kaoyan — components and page-aware clients", () => {
  beforeAll(() => {
    process.env.NEXT_PUBLIC_BASE_PATH = "/kaoyan";
  });
  afterAll(() => {
    if (ORIGINAL_BASE_PATH === undefined) delete process.env.NEXT_PUBLIC_BASE_PATH;
    else process.env.NEXT_PUBLIC_BASE_PATH = ORIGINAL_BASE_PATH;
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    window.history.replaceState(null, "", "/");
  });

  it("knowledge client scopes requests to the resource library on the prefixed library page", async () => {
    vi.resetModules();
    const urls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        urls.push(String(input instanceof Request ? input.url : input));
        return new Response("[]", { status: 200 });
      }),
    );
    window.history.replaceState(null, "", "/kaoyan/knowledge-bases");
    const { listKnowledgeBases } = await import("@/features/knowledge/api/client");
    await listKnowledgeBases({ force: true });
    expect(urls).toHaveLength(1);
    expect(urls[0]).toMatch(/^\/kaoyan\/api\/knowledge-bases\/list\?/);
    expect(urls[0]).toContain("resource_library=true");
  });

  it("knowledge client stays in the workspace scope outside the library page", async () => {
    vi.resetModules();
    const urls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        urls.push(String(input instanceof Request ? input.url : input));
        return new Response("[]", { status: 200 });
      }),
    );
    window.history.replaceState(null, "", "/kaoyan/chat");
    const { listKnowledgeBases } = await import("@/features/knowledge/api/client");
    await listKnowledgeBases({ force: true });
    expect(urls[0]).not.toContain("resource_library=true");
  });

  it("markdown links: root-relative gets basePath, external and hash links pass through", async () => {
    vi.resetModules();
    const { default: RichMarkdownRenderer } = await import(
      "@/components/common/RichMarkdownRenderer"
    );
    const { container } = render(
      <RichMarkdownRenderer
        content={[
          "[doc](/api/knowledge-bases/kb/files/a.pdf#page=2)",
          "[ext](https://example.com/x)",
          "[jump](#section)",
          "[already](/kaoyan/api/x)",
        ].join(" ")}
      />,
    );
    const hrefs = Array.from(container.querySelectorAll("a")).map((a) =>
      a.getAttribute("href"),
    );
    expect(hrefs).toEqual([
      "/kaoyan/api/knowledge-bases/kb/files/a.pdf#page=2",
      "https://example.com/x",
      "#section",
      "/kaoyan/api/x",
    ]);
  });
});
