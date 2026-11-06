import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useAuthStatus } from "@/hooks/useAuthStatus";

const fixture = vi.hoisted(() => ({ status: vi.fn() }));
vi.mock("@/lib/auth", () => ({ fetchAuthStatus: fixture.status }));
afterEach(() => cleanup());

it.each(["admin", "learner"] as const)("derives the admin flag for %s", async (role) => {
  // Spec 2026-11-06: no learning restrictions; every learner writes their own knowledge bases.
  fixture.status.mockResolvedValue({ role });
  const { result } = renderHook(() => useAuthStatus());
  await waitFor(() => expect(result.current.loading).toBe(false));
  expect(result.current.isAdmin).toBe(role === "admin");
  expect(result.current).not.toHaveProperty("allowPrivateKb");
  expect(result.current).not.toHaveProperty("learningPolicy");
});

it("reports an unavailable runtime status", async () => {
  fixture.status.mockResolvedValue(null);
  const { result } = renderHook(() => useAuthStatus());
  await waitFor(() => expect(result.current.loading).toBe(false));
  expect(result.current.statusAvailable).toBe(false);
});
