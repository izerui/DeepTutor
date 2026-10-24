import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useAuthStatus } from "@/hooks/useAuthStatus";

const fixture = vi.hoisted(() => ({ status: vi.fn() }));
vi.mock("@/lib/auth", () => ({ fetchAuthStatus: fixture.status }));
afterEach(() => cleanup());

it.each([
  ["admin", false, true, true],
  ["teacher", false, true, true],
  ["user", false, true, false],
  ["student", false, false, false],
  ["student", true, true, false],
] as const)("derives effective KB write policy for %s (grant=%s)", async (role, grant, allowed, teacher) => {
  fixture.status.mockResolvedValue({
    role, is_teacher: role === "teacher", student_policy: { allow_private_kb: grant },
  });
  const { result } = renderHook(() => useAuthStatus());
  expect(result.current.allowPrivateKb).toBe(false);
  await waitFor(() => expect(result.current.loading).toBe(false));
  expect(result.current.allowPrivateKb).toBe(allowed);
  expect(result.current.isTeacher).toBe(teacher);
});

it("retains the documented non-student fallback if runtime status is unavailable", async () => {
  fixture.status.mockResolvedValue(null);
  const { result } = renderHook(() => useAuthStatus());
  await waitFor(() => expect(result.current.loading).toBe(false));
  expect(result.current.statusAvailable).toBe(false);
  expect(result.current.allowPrivateKb).toBe(true);
});
