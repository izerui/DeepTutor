import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useAuthStatus } from "@/hooks/useAuthStatus";

const fixture = vi.hoisted(() => ({ status: vi.fn() }));
vi.mock("@/lib/auth", () => ({ fetchAuthStatus: fixture.status }));
afterEach(() => cleanup());

it.each([
  ["admin", null, true],
  ["learner", null, true],
  // Only a learning policy (the restricted learning shell) makes knowledge bases read-only.
  ["learner", { allowed_surfaces: ["chat", "reading"] }, false],
] as const)("derives KB write access for %s (learning policy: %j)", async (role, learningPolicy, allowed) => {
  // Spec 2026-11-05: no feature switches; every learner writes their own knowledge bases.
  fixture.status.mockResolvedValue({ role, learning_policy: learningPolicy });
  const { result } = renderHook(() => useAuthStatus());
  expect(result.current.allowPrivateKb).toBe(false);
  await waitFor(() => expect(result.current.loading).toBe(false));
  expect(result.current.allowPrivateKb).toBe(allowed);
  expect(result.current.isAdmin).toBe(role === "admin");
});

it("retains the documented non-student fallback if runtime status is unavailable", async () => {
  fixture.status.mockResolvedValue(null);
  const { result } = renderHook(() => useAuthStatus());
  await waitFor(() => expect(result.current.loading).toBe(false));
  expect(result.current.statusAvailable).toBe(false);
  expect(result.current.allowPrivateKb).toBe(true);
});
