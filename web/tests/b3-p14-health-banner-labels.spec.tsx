import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const fixture = vi.hoisted(() => ({
  health: vi.fn(),
  t: (key: string) => `t(${key})`,
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: fixture.t }) }));
vi.mock("@/lib/book-api", () => ({
  bookApi: { health: fixture.health, refreshFingerprints: vi.fn() },
}));

import BookHealthBanner from "@/app/(workspace)/learning/books/components/BookHealthBanner";

const quiet = { total_entries: 0, error_entries: 0, block_failures: 0 };

afterEach(() => {
  cleanup();
  fixture.health.mockReset();
});

describe("B3-P14 BookHealthBanner KB display labels", () => {
  it("shows display name instead of raw kb: ref for unavailable KBs", async () => {
    fixture.health.mockResolvedValue({
      kb_drift: {
        has_drift: false,
        unavailable_kbs: ["kb:abc123"],
        stale_page_ids: [],
      },
      kb_labels: { "kb:abc123": "Linear Algebra" },
      log_health: quiet,
      generation: null,
    });
    render(<BookHealthBanner bookId="bk_test" refreshKey={1} onRecompile={vi.fn()} />);

    expect(await screen.findByText("Linear Algebra")).toBeTruthy();
    expect(screen.queryByText("kb:abc123")).toBeNull();
  });

  it("shows display names for changed KBs", async () => {
    fixture.health.mockResolvedValue({
      kb_drift: {
        has_drift: true,
        changed_kbs: ["kb:xyz789"],
        unavailable_kbs: [],
        stale_page_ids: ["pg_1"],
      },
      kb_labels: { "kb:xyz789": "Quantum Physics" },
      log_health: quiet,
      generation: null,
    });
    render(<BookHealthBanner bookId="bk_test" refreshKey={1} onRecompile={vi.fn()} />);

    expect(await screen.findByText("Quantum Physics")).toBeTruthy();
    expect(screen.queryByText("kb:xyz789")).toBeNull();
  });

  it("falls back to raw ref when no label available", async () => {
    fixture.health.mockResolvedValue({
      kb_drift: {
        has_drift: false,
        unavailable_kbs: ["kb:nolabel"],
        stale_page_ids: [],
      },
      kb_labels: {},
      log_health: quiet,
      generation: null,
    });
    render(<BookHealthBanner bookId="bk_test" refreshKey={1} onRecompile={vi.fn()} />);

    expect(await screen.findByText("kb:nolabel")).toBeTruthy();
  });

  it("handles missing kb_labels gracefully", async () => {
    fixture.health.mockResolvedValue({
      kb_drift: {
        has_drift: false,
        unavailable_kbs: ["kb:old"],
        stale_page_ids: [],
      },
      // No kb_labels field — old backend compatibility.
      log_health: quiet,
      generation: null,
    });
    render(<BookHealthBanner bookId="bk_test" refreshKey={1} onRecompile={vi.fn()} />);

    expect(await screen.findByText("kb:old")).toBeTruthy();
  });
});
