import { beforeEach, expect, it, vi } from "vitest";
import { bookApi } from "@/lib/book-api";

const fetcher = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({
  apiFetch: fetcher,
  apiUrl: (path: string) => `/kaoyan${path}`,
  wsUrl: (path: string) => `ws://localhost/kaoyan${path}`,
}));
beforeEach(() => fetcher.mockReset().mockImplementation(() => Promise.resolve(Response.json({}))));

it("keeps canonical refs, row workspace and force on management requests", async () => {
  await bookApi.sharing("book:same", "owner-workspace");
  expect(fetcher.mock.lastCall?.[0]).toBe("/kaoyan/api/books/book%3Asame/sharing?dt_workspace=owner-workspace");
  await bookApi.references("book:same", "owner-workspace");
  expect(fetcher.mock.lastCall?.[0]).toBe("/kaoyan/api/books/book%3Asame/references?dt_workspace=owner-workspace");
  const body = { visibility: "public" as const, kb_choices: { kb1: "reference_only" as const } };
  await bookApi.setVisibility("book:same", body, "owner-workspace");
  expect(fetcher.mock.lastCall?.[1].body).toBe(JSON.stringify(body));
  await bookApi.delete("book:same", "owner-workspace", { force: true });
  expect(fetcher.mock.lastCall?.[0]).toBe("/kaoyan/api/books/book%3Asame?force=true&dt_workspace=owner-workspace");
  await bookApi.delete("same", "local");
  expect(fetcher.mock.lastCall?.[0]).toBe("/kaoyan/api/books/same?dt_workspace=local");
});
