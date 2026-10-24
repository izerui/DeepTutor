import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  createKnowledgeBase, deleteKnowledgeBase, deleteKbFile, KbFileDeleteBlockedError,
  listKnowledgeBaseReferences, readErrorDetail, setKnowledgeBaseVisibility,
} from "@/features/knowledge/api/client";

const fixture = vi.hoisted(() => ({ fetch: vi.fn(), invalidate: vi.fn() }));
vi.mock("@/lib/api", () => ({
  apiFetch: fixture.fetch,
  apiUrl: (path: string) => `/kaoyan${path}`,
}));
vi.mock("@/lib/client-cache", () => ({
  invalidateClientCache: fixture.invalidate,
  withClientCache: (_: string, load: () => unknown) => load(),
}));
beforeEach(() => { fixture.fetch.mockReset(); fixture.invalidate.mockReset(); });
afterEach(() => vi.restoreAllMocks());

it("sends stable visibility/reference/delete requests through context-path helpers", async () => {
  fixture.fetch.mockResolvedValueOnce(Response.json({ kb_id: "id", ref: "kb:id", visibility: "private" }));
  await setKnowledgeBaseVisibility("kb:id", "private", { force: true });
  expect(fixture.fetch).toHaveBeenLastCalledWith("/kaoyan/api/knowledge-bases/kb%3Aid/visibility", expect.objectContaining({
    method: "PUT", body: JSON.stringify({ visibility: "private", force: true }),
  }));
  fixture.fetch.mockResolvedValueOnce(Response.json({ references: [{ referrer_id: "reader" }] }));
  expect(await listKnowledgeBaseReferences("kb:id")).toEqual([{ referrer_id: "reader" }]);
  expect(fixture.fetch.mock.lastCall?.[0]).toBe("/kaoyan/api/knowledge-bases/kb%3Aid/references");
  fixture.fetch.mockResolvedValueOnce(new Response(null, { status: 204 }));
  await deleteKnowledgeBase("kb:id", { force: true });
  expect(fixture.fetch.mock.lastCall?.[1].body).toBe('{"name":"kb:id","force":true}');
});

it("preserves structured guard references and does not retry deletes automatically", async () => {
  const references = [{ referrer_kind: "partner", referrer_id: "reader" }];
  fixture.fetch.mockResolvedValueOnce(Response.json({
    detail: { code: "requires_confirmation", message: "Confirm references", references },
  }, { status: 409 }));
  await expect(deleteKbFile("kb:id", "folder/a.md")).rejects.toMatchObject({
    name: "KbFileDeleteBlockedError", code: "requires_confirmation", references,
  });
  expect(fixture.fetch).toHaveBeenCalledTimes(1);
  expect(fixture.invalidate).not.toHaveBeenCalled();
  fixture.fetch.mockResolvedValueOnce(Response.json({ was_indexed: false }));
  await deleteKbFile("kb:id", "folder/a.md", { confirm: true, force: true });
  expect(fixture.fetch.mock.lastCall?.[0]).toBe("/kaoyan/api/knowledge-bases/kb%3Aid/files/folder/a.md?confirm=true&force=true");
  expect(new KbFileDeleteBlockedError("blocked", "document_referenced", [])).toBeInstanceOf(Error);
});

it("reports structured messages and includes creation visibility", async () => {
  expect(await readErrorDetail(Response.json({ detail: { message: "Referenced material" } }), "fallback")).toBe("Referenced material");
  fixture.fetch.mockResolvedValueOnce(Response.json({ ref: "kb:new", kb_id: "new" }));
  const result = await createKnowledgeBase({ name: "new", provider: "pageindex", files: [], visibility: "public" });
  const body = fixture.fetch.mock.lastCall?.[1].body as FormData;
  expect(body.get("visibility")).toBe("public");
  expect(result.ref).toBe("kb:new");
});
