import { beforeEach, expect, it, vi } from "vitest";
import {
  describeOtherReferences,
  describeWithdrawalImpact,
  parseOtherReferences,
  parseWithdrawalImpact,
  runWithWithdrawalConfirmation,
  WithdrawalConfirmationError,
} from "@/lib/withdrawal-confirmation";

const fixture = vi.hoisted(() => ({ fetch: vi.fn() }));
vi.mock("@/lib/api", () => ({
  apiFetch: fixture.fetch,
  apiUrl: (path: string) => path,
  wsUrl: (path: string) => path,
}));
vi.mock("@/lib/workspace-scope", () => ({
  activeWorkspaceId: () => "",
  scopedUrl: (path: string) => path,
}));

const t = (key: string, values?: Record<string, unknown>) =>
  key.replace(/\{\{(\w+)}}/g, (_, name: string) => String(values?.[name] ?? name));

beforeEach(() => fixture.fetch.mockReset());

it("parses only the counts-only teacher-path confirmation code", () => {
  expect(parseWithdrawalImpact({
    code: "referenced_by_teacher_paths", affected_paths: 3, affected_students: 11,
  })).toEqual({ affectedPaths: 3, affectedStudents: 11 });
  expect(parseWithdrawalImpact({ code: "book_referenced", affected_paths: 3 })).toBeNull();
  expect(parseWithdrawalImpact("referenced_by_teacher_paths")).toBeNull();
  expect(parseWithdrawalImpact({ code: "referenced_by_teacher_paths", affected_paths: -1 }))
    .toEqual({ affectedPaths: 0, affectedStudents: 0 });
  expect(parseOtherReferences({ total: 2, by_kind: { course: 2 } })).toEqual({ total: 2, by_kind: { course: 2 } });
  expect(parseOtherReferences(undefined)).toEqual({ total: 0, by_kind: {} });
});

it("describes the impact with counts only", () => {
  expect(describeWithdrawalImpact({ affectedPaths: 1, affectedStudents: 2 }, t)).toBe(
    "1 published teacher paths use this material\n2 students assigned\n"
      + "Those paths will show this material as unavailable. Continue?",
  );
  expect(describeOtherReferences({ total: 0, by_kind: {} }, t)).toBe("");
  expect(describeOtherReferences({ total: 4, by_kind: { partner: 4 } }, t)).toBe(
    "4 items from other accounts use this material and will show it as unavailable.",
  );
});

it("book API keeps the structured 409 detail and sends confirm on delete/visibility", async () => {
  const { bookApi, bookWithdrawalImpact } = await import("@/lib/book-api");
  fixture.fetch.mockResolvedValueOnce(Response.json({ detail: {
    code: "referenced_by_teacher_paths", affected_paths: 1, affected_students: 6, message: "Confirm",
  } }, { status: 409 }));
  const failure = await bookApi.delete("book:b", "").catch((error: unknown) => error);
  expect(bookWithdrawalImpact(failure)).toEqual({ affectedPaths: 1, affectedStudents: 6 });
  expect(bookWithdrawalImpact(new Error("other"))).toBeNull();

  fixture.fetch.mockResolvedValueOnce(Response.json({ deleted: true, book_id: "b" }));
  await bookApi.delete("book:b", "", { confirm: true });
  expect(fixture.fetch.mock.lastCall?.[0]).toMatch(/\/books\/book%3Ab\?confirm=true$/);
  fixture.fetch.mockResolvedValueOnce(Response.json({ deleted: true, book_id: "b" }));
  await bookApi.delete("book:b", "", { force: true });
  expect(fixture.fetch.mock.lastCall?.[0]).toMatch(/\/books\/book%3Ab\?force=true$/);

  fixture.fetch.mockResolvedValueOnce(Response.json({ book_id: "b", ref: "book:b", visibility: "private", kbs: {} }));
  await bookApi.setVisibility("book:b", { visibility: "private", confirm: true }, "");
  expect(JSON.parse(fixture.fetch.mock.lastCall?.[1].body)).toEqual({ visibility: "private", confirm: true });
});

it("runs once, asks with the counts, and repeats confirmed only when accepted", async () => {
  const needs = new WithdrawalConfirmationError("confirm", { affectedPaths: 1, affectedStudents: 3 });
  for (const accept of [true, false]) {
    const run = vi.fn().mockRejectedValueOnce(needs).mockResolvedValue(undefined);
    const ask = vi.fn().mockReturnValue(accept);
    await runWithWithdrawalConfirmation(run, t, ask);
    expect(ask).toHaveBeenCalledWith(describeWithdrawalImpact(needs.impact, t));
    expect(run.mock.calls).toEqual(accept ? [[false], [true]] : [[false]]);
  }
  const plain = vi.fn().mockResolvedValue(undefined);
  const never = vi.fn();
  await runWithWithdrawalConfirmation(plain, t, never);
  expect(plain.mock.calls).toEqual([[false]]);
  expect(never).not.toHaveBeenCalled();
  await expect(runWithWithdrawalConfirmation(() => Promise.reject(new Error("boom")), t, never))
    .rejects.toThrow("boom");
});
