import { act, renderHook, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { useSessionThreads } from "@/hooks/useSessionThreads";
import { listSessionThreads, type SessionSummary } from "@/lib/session-api";

vi.mock("@/lib/session-api", () => ({ listSessionThreads: vi.fn() }));

it("keeps the last list during a network failure, retries online and clears inaccessible rows", async () => {
  const rows = [{ session_id: "thread" }] as SessionSummary[];
  vi.mocked(listSessionThreads)
    .mockResolvedValueOnce(rows)
    .mockRejectedValueOnce(new TypeError("offline"))
    .mockResolvedValueOnce(rows)
    .mockRejectedValueOnce({ status: 403 });
  const { result } = renderHook(() => useSessionThreads("chat"));
  await waitFor(() => expect(result.current.loaded).toBe(true));
  act(() => result.current.refresh());
  await waitFor(() => expect(result.current.failed).toBe(true));
  expect(result.current.threads).toEqual(rows);
  act(() => { window.dispatchEvent(new Event("online")); });
  await waitFor(() => expect(result.current.loaded).toBe(true));
  act(() => result.current.refresh());
  await waitFor(() => expect(result.current.failed).toBe(true));
  expect(result.current.threads).toEqual([]);
});
