import { afterEach, expect, it, vi } from "vitest";

afterEach(() => {
  window.history.replaceState({}, "", "/");
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.resetModules();
});

it.each([
  ["", { confirm: true }],
  ["/kaoyan", { confirm: true }],
  ["/kaoyan", { force: true }],
  ["/kaoyan", { confirm: true, force: true }],
] as const)("merges deletion flags with resource-library and workspace queries under %s", async (basePath, options) => {
  vi.stubEnv("NEXT_PUBLIC_BASE_PATH", basePath);
  vi.resetModules();
  window.history.replaceState({}, "", `${basePath}/knowledge-bases/kb%3Aown?dt_workspace=workspace-a`);
  const fetch = vi.fn().mockResolvedValue(Response.json({ was_indexed: false }));
  vi.stubGlobal("fetch", fetch);
  const { deleteKbFile } = await import("@/features/knowledge/api/client");
  await deleteKbFile("kb:own", "folder/doc?.md", options);
  const [input, init] = fetch.mock.calls[0];
  const url = new URL(input, window.location.origin);
  expect(url.pathname).toBe(`${basePath}/api/knowledge-bases/kb%3Aown/files/folder/doc%3F.md`);
  expect(url.searchParams.get("resource_library")).toBe("true");
  expect(url.searchParams.get("dt_workspace")).toBe("workspace-a");
  for (const [key, value] of Object.entries(options)) {
    expect(url.searchParams.get(key)).toBe(String(value));
  }
  expect(init.method).toBe("DELETE");
  expect(fetch).toHaveBeenCalledOnce();
});
