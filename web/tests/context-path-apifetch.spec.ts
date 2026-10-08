/**
 * apiFetch context-path tests — run in jsdom but restore Node's native
 * Request to bypass Vitest's createCompatRequest wrapper that drops
 * method/body when constructing Request(url, existingRequest).
 *
 * Root cause: vitest/dist/chunks/index.DC7d2Pf8.js:543 spreads the init
 * object with `{ ...init }` when body is non-null; when init is a Request,
 * prototype getters (method, headers, body) are not enumerable and fall
 * back to defaults (GET, empty).
 */
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

// Capture Node's native Request before Vitest's jsdom wrapper replaces it.
// In vitest jsdom, globalThis.Request is already wrapped, but we can reach
// the original via the prototype chain.
import { Request as NodeRequest } from "undici";

describe("apiFetch context-path with native Request", () => {
  const origRequest = globalThis.Request;

  beforeAll(() => {
    process.env.NEXT_PUBLIC_BASE_PATH = "/kaoyan";
    // Restore native Request for this suite so method/body/headers
    // are preserved in Request(url, existingRequest).
    globalThis.Request = NodeRequest as unknown as typeof globalThis.Request;
  });
  afterAll(() => {
    delete process.env.NEXT_PUBLIC_BASE_PATH;
    globalThis.Request = origRequest;
  });

  it("apiFetch with string input prefixes the URL", async () => {
    vi.resetModules();
    const fetchSpy = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchSpy);

    const { apiFetch } = await import("@/shared/api/client");
    await apiFetch("/api/reading/materials");

    const actual = fetchSpy.mock.calls[0][0] as string;
    expect(typeof actual).toBe("string");
    expect(actual).toMatch(/^\/kaoyan\/api\/reading\/materials/);
    vi.unstubAllGlobals();
  });

  it("apiFetch with same-origin URL input prefixes the pathname", async () => {
    vi.resetModules();
    const fetchSpy = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchSpy);

    const { apiFetch } = await import("@/shared/api/client");
    await apiFetch(new URL("/api/books", window.location.origin));

    const actual = fetchSpy.mock.calls[0][0] as URL;
    expect(actual).toBeInstanceOf(URL);
    expect(actual.pathname).toBe("/kaoyan/api/books");
    vi.unstubAllGlobals();
  });

  it("apiFetch with cross-origin URL input does not modify it", async () => {
    vi.resetModules();
    const fetchSpy = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchSpy);

    const { apiFetch } = await import("@/shared/api/client");
    await apiFetch(new URL("https://external.example.com/api/data"));

    const actual = fetchSpy.mock.calls[0][0] as URL;
    expect(actual.origin).toBe("https://external.example.com");
    expect(actual.pathname).toBe("/api/data");
    vi.unstubAllGlobals();
  });

  it("apiFetch with Request input prefixes URL and preserves method, body, headers", async () => {
    vi.resetModules();
    const fetchSpy = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchSpy);

    const { apiFetch } = await import("@/shared/api/client");
    const input = new Request(new URL("/api/settings", window.location.origin), {
      method: "POST",
      body: '{"key":"val"}',
      headers: { "Content-Type": "application/json", "X-Custom": "test" },
    });
    await apiFetch(input);

    expect(fetchSpy).toHaveBeenCalledOnce();
    const actual = fetchSpy.mock.calls[0][0] as Request;
    expect(actual).toBeInstanceOf(Request);

    // URL prefixing
    expect(new URL(actual.url).pathname).toBe("/kaoyan/api/settings");

    // method preservation — unconditional assertion
    expect(actual.method).toBe("POST");

    // body preservation
    const body = await actual.text();
    expect(body).toBe('{"key":"val"}');

    // headers preservation
    expect(actual.headers.get("content-type")).toBe("application/json");
    expect(actual.headers.get("x-custom")).toBe("test");

    vi.unstubAllGlobals();
  });
});
