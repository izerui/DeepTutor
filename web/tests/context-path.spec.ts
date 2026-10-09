import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

const ORIGINAL_BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH;

describe("context-path /kaoyan", () => {
  beforeAll(() => {
    process.env.NEXT_PUBLIC_BASE_PATH = "/kaoyan";
  });
  afterAll(() => {
    if (ORIGINAL_BASE_PATH === undefined) delete process.env.NEXT_PUBLIC_BASE_PATH;
    else process.env.NEXT_PUBLIC_BASE_PATH = ORIGINAL_BASE_PATH;
  });

  // --- browserPath ---

  it("browserPath prepends basePath to relative paths", async () => {
    vi.resetModules();
    const { browserPath } = await import("@/lib/workspace-scope");
    expect(browserPath("/api/auth/status")).toBe("/kaoyan/api/auth/status");
    expect(browserPath("/ws")).toBe("/kaoyan/ws");
    expect(browserPath("/files/outputs/a.pdf")).toBe("/kaoyan/files/outputs/a.pdf");
  });

  it("browserPath leaves protocol-relative URLs unchanged", async () => {
    vi.resetModules();
    const { browserPath } = await import("@/lib/workspace-scope");
    expect(browserPath("//external.example.com/file")).toBe("//external.example.com/file");
  });

  it("browserPath prepends basePath to same-origin absolute URLs", async () => {
    vi.resetModules();
    const { browserPath } = await import("@/lib/workspace-scope");
    const origin = window.location.origin;
    expect(browserPath(`${origin}/api/test`)).toBe(`${origin}/kaoyan/api/test`);
  });

  it("browserPath leaves cross-origin absolute URLs unchanged", async () => {
    vi.resetModules();
    const { browserPath } = await import("@/lib/workspace-scope");
    expect(browserPath("https://external.example.com/api/test")).toBe("https://external.example.com/api/test");
  });

  it("browserPath is idempotent (no double prefix)", async () => {
    vi.resetModules();
    const { browserPath } = await import("@/lib/workspace-scope");
    expect(browserPath("/kaoyan/api/test")).toBe("/kaoyan/api/test");
    expect(browserPath("/kaoyan?dt_workspace=x")).toBe("/kaoyan?dt_workspace=x");
    const origin = window.location.origin;
    expect(browserPath(`${origin}/kaoyan/api/test`)).toBe(`${origin}/kaoyan/api/test`);
  });

  it("browserPath does not match partial prefix like /kaoyan-other", async () => {
    vi.resetModules();
    const { browserPath } = await import("@/lib/workspace-scope");
    expect(browserPath("/kaoyan-other/api")).toBe("/kaoyan/kaoyan-other/api");
    expect(browserPath("/kaoyan-other?x=1")).toBe("/kaoyan/kaoyan-other?x=1");
  });

  // --- assetPath ---

  it("assetPath adds basePath to static resource paths", async () => {
    vi.resetModules();
    const { assetPath } = await import("@/lib/workspace-scope");
    expect(assetPath("/logo.png")).toBe("/kaoyan/logo.png");
    expect(assetPath("/agent-icons/kimi.svg")).toBe("/kaoyan/agent-icons/kimi.svg");
    expect(assetPath("/kaoyan/logo.png")).toBe("/kaoyan/logo.png"); // idempotent
    expect(assetPath("//cdn.example.com/logo.png")).toBe("//cdn.example.com/logo.png");
    expect(assetPath("/kaoyan-other/logo.png")).toBe("/kaoyan/kaoyan-other/logo.png");
  });

  // --- stripBasePath ---

  it("stripBasePath removes prefix for Next.js navigation", async () => {
    vi.resetModules();
    const { stripBasePath } = await import("@/lib/workspace-scope");
    expect(stripBasePath("/kaoyan/learning/books/123")).toBe("/learning/books/123");
    expect(stripBasePath("/learning/books/123")).toBe("/learning/books/123");
    expect(stripBasePath("/kaoyan")).toBe("/");
    expect(stripBasePath("/kaoyan-other")).toBe("/kaoyan-other"); // no partial match
    expect(stripBasePath("/kaoyan?next=x")).toBe("/?next=x");
    expect(stripBasePath("/kaoyan/chat?next=x")).toBe("/chat?next=x");
    expect(stripBasePath("/kaoyan#hash")).toBe("/#hash");
    expect(stripBasePath("/kaoyan/chat#hash")).toBe("/chat#hash");
  });

  // --- scopedUrl ---

  it("scopedUrl does NOT add basePath (safe for router.push)", async () => {
    vi.resetModules();
    const { scopedUrl } = await import("@/lib/workspace-scope");
    const result = scopedUrl("/learning/books/123");
    expect(result).toMatch(/^\/learning\/books\/123/);
    expect(result).not.toContain("/kaoyan");
  });

  // --- apiUrl / wsUrl ---

  it("apiUrl and wsUrl include basePath for browser requests", async () => {
    vi.resetModules();
    const { apiUrl, wsUrl } = await import("@/shared/api/client");
    expect(apiUrl("/api/auth/status")).toMatch(/^\/kaoyan\/api\/auth\/status/);
    expect(wsUrl("/ws")).toMatch(/^\/kaoyan\/ws/);
  });

  // --- loginHref / browserReturnPath ---

  it("loginHref builds correct basePath-prefixed URL with stripped next param", async () => {
    vi.resetModules();
    const { loginHref, browserReturnPath } = await import("@/shared/auth/return-url");

    // Deep page
    const returnPath = browserReturnPath({ pathname: "/kaoyan/learning/books/123" });
    expect(returnPath).toBe("/learning/books/123");
    const href = loginHref(returnPath);
    expect(href).toMatch(/^\/kaoyan\/login\?/);
    expect(href).toContain("next=%2Flearning%2Fbooks%2F123");

    // Root with query string — the key edge case
    const rootReturn = browserReturnPath({ pathname: "/kaoyan", search: "?workspace=ws1" });
    expect(rootReturn).toBe("/?workspace=ws1");
    const rootHref = loginHref(rootReturn);
    expect(rootHref).toMatch(/^\/kaoyan\/login\?/);
    expect(rootHref).not.toContain("/kaoyan/kaoyan");
  });

  // --- task board live stream ---

  it("task board EventSource connects under basePath", async () => {
    vi.resetModules();
    const urls: string[] = [];
    vi.stubGlobal(
      "EventSource",
      class {
        onmessage: unknown = null;
        onerror: unknown = null;
        onopen: unknown = null;
        close = vi.fn();
        constructor(url: string) {
          urls.push(url);
        }
      },
    );
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ tasks: [], revision: 0 }))),
    );
    try {
      const { startTaskBoardSync } = await import("@/lib/task-board-store");
      const stop = startTaskBoardSync();
      stop();
      expect(urls).toHaveLength(1);
      expect(urls[0]).toMatch(/^\/kaoyan\/api\/task-board\/events/);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
