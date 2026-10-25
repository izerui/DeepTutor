import { expect, it, vi } from "vitest";
import { fetchBookPermission, saveBookPermission } from "@/features/multi-user/api";
import type { BookPermission } from "@/features/multi-user/types";

const fetcher = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({
  apiFetch: fetcher,
  apiUrl: (path: string) => `/kaoyan${path}`,
}));

it("encodes the user, retains context path and sends only create even for a legacy object", async () => {
  fetcher.mockImplementation(async () => Response.json({ permission: { create: false } }));
  expect(await fetchBookPermission("user/a")).toEqual({ create: false });
  expect(fetcher).toHaveBeenLastCalledWith("/kaoyan/api/multi-user/users/user%2Fa/book-permission");
  await saveBookPermission("user/a", { create: false, books: { old: "edit" }, default: "read" } as BookPermission);
  expect(fetcher).toHaveBeenLastCalledWith("/kaoyan/api/multi-user/users/user%2Fa/book-permission", {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: '{"create":false}',
  });
});

it("preserves load and save errors", async () => {
  fetcher.mockImplementation(async () => Response.json({ detail: "Denied" }, { status: 403 }));
  await expect(fetchBookPermission("a")).rejects.toThrow("Denied");
  await expect(saveBookPermission("a", { create: false })).rejects.toThrow("Denied");
});
