import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ apiFetch, apiUrl: (url: string) => url }));

import {
  MasteryApiError,
  fetchMasteryTopic,
  fetchProgressEvents,
  isCourseUnavailable,
  redoProgress,
} from "@/lib/learning-api";

const CLOSED = {
  code: "teacher_path_unavailable",
  message: "This course is no longer available to you.",
};

async function failure(run: () => Promise<unknown>): Promise<unknown> {
  try {
    await run();
  } catch (error) {
    return error;
  }
  throw new Error("expected the request to fail");
}

describe("a closed teacher path is recognised by its code, not by its id", () => {
  beforeEach(() => apiFetch.mockReset());

  it.each([
    ["topic", () => fetchMasteryTopic("assigned_tpl1")],
    ["events", () => fetchProgressEvents("assigned_tpl1")],
    ["redo", () => redoProgress("assigned_tpl1")],
  ])("treats a 403 carrying the unavailable code as closed (%s)", async (_name, run) => {
    apiFetch.mockResolvedValue(Response.json({ detail: CLOSED }, { status: 403 }));
    const error = await failure(run);
    expect(error).toBeInstanceOf(MasteryApiError);
    expect((error as MasteryApiError).code).toBe("teacher_path_unavailable");
    expect(isCourseUnavailable(error, "assigned_tpl1")).toBe(true);
  });

  it("keeps any other 403 on a teacher path as its own error", async () => {
    apiFetch.mockResolvedValue(
      Response.json({ detail: "Your account cannot use learning paths." }, { status: 403 }),
    );
    const error = await failure(() => fetchMasteryTopic("assigned_tpl1"));
    expect(isCourseUnavailable(error, "assigned_tpl1")).toBe(false);
    expect((error as Error).message).toContain("Your account cannot use learning paths.");
  });

  it("shows the server's message, not an object, for a coded refusal", async () => {
    apiFetch.mockResolvedValue(Response.json({ detail: CLOSED }, { status: 403 }));
    const error = await failure(() => fetchMasteryTopic("assigned_tpl1"));
    expect((error as Error).message).toContain(CLOSED.message);
    expect((error as Error).message).not.toContain("[object Object]");
  });

  it("never reads a bare status or a non-JSON body as closed", async () => {
    apiFetch.mockResolvedValue(new Response("Forbidden by proxy", { status: 403 }));
    expect(isCourseUnavailable(await failure(() => fetchMasteryTopic("assigned_tpl1")), "assigned_tpl1")).toBe(false);
    expect(isCourseUnavailable(new MasteryApiError("x", 403), "assigned_tpl1")).toBe(false);
    expect(isCourseUnavailable(new MasteryApiError("x", 404, "teacher_path_unavailable"), "assigned_tpl1")).toBe(false);
    expect(isCourseUnavailable(new Error("x"), "assigned_tpl1")).toBe(false);
  });
});
