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

describe("B3-P10 BookHealthBanner unavailable knowledge bases", () => {
  it("shows a withdrawn KB as no longer available, not as updated", async () => {
    fixture.health.mockResolvedValue({
      kb_drift: {
        has_drift: false,
        new_kbs: [],
        removed_kbs: [],
        changed_kbs: [],
        unavailable_kbs: ["kb:optics"],
        stale_page_ids: [],
      },
      log_health: quiet,
      generation: null,
    });
    render(<BookHealthBanner bookId="bk_lens" refreshKey={1} onRecompile={vi.fn()} />);

    expect(await screen.findByText("t(No longer available)", { exact: false })).toBeTruthy();
    expect(screen.getByText("kb:optics")).toBeTruthy();
    expect(screen.queryByText("t(Updated)", { exact: false })).toBeNull();
    expect(screen.queryByText("t(Mark as seen)")).toBeNull();
  });

  it("still lists real updates next to an unavailable KB", async () => {
    fixture.health.mockResolvedValue({
      kb_drift: {
        has_drift: true,
        changed_kbs: ["kb:waves"],
        unavailable_kbs: ["kb:optics"],
        stale_page_ids: ["pg_one"],
      },
      log_health: quiet,
      generation: null,
    });
    render(<BookHealthBanner bookId="bk_lens" refreshKey={1} onRecompile={vi.fn()} />);

    expect(await screen.findByText("kb:waves")).toBeTruthy();
    expect(screen.getByText("kb:optics")).toBeTruthy();
    expect(screen.getByText("t(Updated)", { exact: false })).toBeTruthy();
    expect(screen.getByText("t(No longer available)", { exact: false })).toBeTruthy();
  });
});
