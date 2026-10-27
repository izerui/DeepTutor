import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { MasteryTopicSocketHandlers } from "@/lib/mastery-ws";

const sockets = vi.hoisted(
  () => [] as Array<{ handlers: MasteryTopicSocketHandlers }>,
);

vi.mock("@/lib/mastery-ws", () => ({
  MasteryTopicSocket: class {
    constructor(_pathId: string, handlers: MasteryTopicSocketHandlers) {
      sockets.push({ handlers });
    }
    start() {}
    stop() {}
    wake() {}
  },
}));

vi.mock("@/lib/learning-api", () => ({
  fetchProgressEvents: vi.fn(() => new Promise(() => {})),
  isCourseUnavailable: () => false,
}));

import { useMasteryPathActivity } from "@/hooks/useMasteryPathActivity";

describe("useMasteryPathActivity", () => {
  afterEach(() => {
    sockets.length = 0;
  });

  it("shows the course as unavailable when the live socket says it closed", () => {
    const { result } = renderHook(() => useMasteryPathActivity("assigned_t1"));
    expect(result.current.unavailable).toBe(false);

    act(() => sockets[0].handlers.onLive?.());
    act(() => sockets[0].handlers.onUnavailable?.());
    expect(result.current.unavailable).toBe(true);
    expect(result.current.connection).toBe("offline");

    // A later socket state change never reopens the closed course.
    act(() => sockets[0].handlers.onConnecting?.());
    expect(result.current.unavailable).toBe(true);
  });
});
