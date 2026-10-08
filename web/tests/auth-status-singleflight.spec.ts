import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchAuthStatus,
  invalidateAuthStatusCache,
  login,
  logout,
  register,
} from "@/lib/auth";
import { invalidateClientCache, withClientCache } from "@/lib/client-cache";

describe("fetchAuthStatus single-flight cache", () => {
  afterEach(() => {
    invalidateAuthStatusCache();
    invalidateClientCache("");
    vi.unstubAllGlobals();
  });

  it("shares concurrent and near-term status reads", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({
          enabled: true,
          authenticated: true,
          role: "admin",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const [first, second] = await Promise.all([
      fetchAuthStatus(),
      fetchAuthStatus(),
    ]);
    const cached = await fetchAuthStatus();

    expect(first?.role).toBe("admin");
    expect(second).toEqual(first);
    expect(cached).toEqual(first);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("can be invalidated after an auth mutation", async () => {
    const fetchMock = vi.fn(async () =>
      new Response(JSON.stringify({ enabled: false, authenticated: false }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchAuthStatus();
    invalidateAuthStatusCache();
    await fetchAuthStatus();

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each(["login", "logout", "register"])(
    "clears account data on %s, including late cache writes",
    async (mutation) => {
      vi.stubGlobal("window", {
        location: { search: "", origin: "http://localhost" },
      });
      vi.stubGlobal("fetch", vi.fn(async () =>
        new Response("{}", { status: 200 }),
      ));
      const load = vi.fn(async () => ["admin-only-kb"]);
      const keys = [
        "knowledge:list", "courses:list", "sessions:20:0:account",
        "workspaces:list", "imports:history", "llm-options:list",
        "skills:list", "personas:list", "subagents:settings",
        "capabilities:catalog",
      ];
      for (const key of keys) await withClientCache(key, load);
      let resolveOld!: (value: string[]) => void;
      const oldRequest = withClientCache("knowledge:files", () =>
        new Promise<string[]>((resolve) => { resolveOld = resolve; }),
      );

      if (mutation === "login") expect((await login("learner", "password")).ok).toBe(true);
      else if (mutation === "register") expect((await register("learner", "password")).ok).toBe(true);
      else await logout();

      resolveOld(["admin-only-file"]);
      await oldRequest;
      const loadLearner = vi.fn(async () => []);
      for (const key of keys) {
        expect(await withClientCache(key, loadLearner)).toEqual([]);
      }
      expect(await withClientCache("knowledge:files", loadLearner)).toEqual([]);
      expect(loadLearner).toHaveBeenCalledTimes(keys.length + 1);
    },
  );

  it("does not restore an old identity or clear a new request after invalidation", async () => {
    let resolveOld!: (value: Response) => void;
    let resolveNew!: (value: Response) => void;
    const fetchMock = vi.fn()
      .mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveOld = resolve; }))
      .mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveNew = resolve; }));
    vi.stubGlobal("fetch", fetchMock);
    const oldRequest = fetchAuthStatus();
    invalidateAuthStatusCache();
    const newRequest = fetchAuthStatus();
    resolveOld(new Response(JSON.stringify({ enabled: true, authenticated: true, role: "admin" })));
    expect(await oldRequest).toBeNull();
    expect(fetchAuthStatus()).toBe(newRequest);
    resolveNew(new Response(JSON.stringify({ enabled: true, authenticated: true, role: "user" })));
    expect((await newRequest)?.role).toBe("user");
    expect((await fetchAuthStatus())?.role).toBe("user");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
