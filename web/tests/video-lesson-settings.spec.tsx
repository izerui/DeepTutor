/**
 * Spec 2026-11-20 §2–§4: the admin-only Video Lesson settings page shows the
 * runtime, stages edits through the settings draft, and saves them to the
 * admin endpoint.
 */
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const fixture = vi.hoisted(() => ({
  registered: new Map<string, { dirty: boolean; payload: unknown; save: () => Promise<void> } | null>(),
  install: vi.fn(),
  status: {
    runtime: {
      available: true,
      reason: "ok",
      node: "/usr/bin/node",
      node_version: "v22.22.0",
      npm: "/usr/bin/npm",
      installed: false,
      runtime_dir: "/data/system/runtime/remotion",
    },
    min_node_major: 22,
    browser: { available: false, path: null },
    npm_registry: "",
    max_attempts_limit: 6,
  },
}));

const i18n = vi.hoisted(() => ({
  t: (key: string, options?: Record<string, unknown>) =>
    key.replace(/\{\{(\w+)\}\}/g, (_m: string, name: string) => String(options?.[name] ?? "")),
  i18n: { language: "en" },
}));
vi.mock("react-i18next", () => ({ useTranslation: () => i18n }));
const store = vi.hoisted(() => ({
  draftRevision: 0,
  pendingExtensionPayload: (key: string) => fixture.registered.get(key)?.payload,
  registerExtension: (key: string, ext: never) => {
    fixture.registered.set(key, ext);
  },
}));
vi.mock("@/features/settings/store/SettingsStore", () => ({
  useSettings: () => store,
}));
vi.mock("@/components/settings/shared", () => ({
  SettingsPageHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
  SettingSection: ({ title, children }: { title: string; children: React.ReactNode }) => (
    <section aria-label={title}>{children}</section>
  ),
  SettingRow: ({ title, description, control }: { title: string; description?: string; control: React.ReactNode }) => (
    <div>
      <span>{title}</span>
      {description ? <p>{description}</p> : null}
      {control}
    </div>
  ),
}));
vi.mock("@/lib/video-lesson-admin-api", () => ({
  getVideoLessonSettings: vi.fn(async () => ({
    learners_allowed: true,
    temperature: 0.5,
    max_tokens: 24000,
    max_attempts: 3,
    layout_check: false,
    npm_registry: "",
  })),
  getVideoLessonStatus: vi.fn(async () => fixture.status),
  installVideoLessonRuntime: fixture.install,
}));
const applied = vi.hoisted(() => [] as Array<[string, unknown]>);
vi.mock("@/lib/settings-extensions", () => ({
  applyExtensionPayload: async (key: string, payload: unknown) => {
    applied.push([key, payload]);
  },
}));

import VideoLessonSettingsPage from "@/features/settings/sections/VideoLessonSettingsSection";

afterEach(() => {
  cleanup();
  fixture.registered.clear();
  applied.length = 0;
});

describe("Video Lesson admin settings", () => {
  it("shows the runtime state and the skipped browser check", async () => {
    render(<VideoLessonSettingsPage />);
    expect(
      await screen.findByText("Node.js is ready. The renderer packages are installed on first use."),
    ).toBeTruthy();
    expect(screen.getByText("No Chrome or Chromium found; the check is skipped.")).toBeTruthy();
    expect(screen.getByText(/v22\.22\.0/)).toBeTruthy();
  });

  it("stages toggles, budgets and the npm mirror for the admin endpoint", async () => {
    const { rerender } = render(<VideoLessonSettingsPage />);
    const learners = await screen.findByRole("switch", { name: "Learners can use Video Lesson" });
    expect(learners.getAttribute("aria-checked")).toBe("true");
    fireEvent.click(learners);
    rerender(<VideoLessonSettingsPage />);
    const layout = screen.getByRole("switch", { name: "Check layout in a headless browser" });
    expect(layout.getAttribute("aria-checked")).toBe("false");
    fireEvent.click(layout);
    rerender(<VideoLessonSettingsPage />);
    fireEvent.change(screen.getByLabelText("Max attempts"), { target: { value: "2" } });
    rerender(<VideoLessonSettingsPage />);
    fireEvent.change(screen.getByLabelText("npm registry mirror"), {
      target: { value: "https://registry.npmmirror.com" },
    });
    const staged = fixture.registered.get("video-lesson");
    expect(staged?.dirty).toBe(true);
    expect(staged?.payload).toMatchObject({
      learners_allowed: false,
      layout_check: true,
      max_attempts: 2,
      npm_registry: "https://registry.npmmirror.com",
    });
    await act(async () => {
      await staged?.save();
    });
    expect(applied[0][0]).toBe("video-lesson");
  });

  it("installs the renderer on request", async () => {
    fixture.install.mockResolvedValueOnce({
      ...fixture.status,
      runtime: { ...fixture.status.runtime, installed: true },
    });
    render(<VideoLessonSettingsPage />);
    fireEvent.click(await screen.findByText("Install now"));
    await waitFor(() => expect(screen.getByText("The renderer packages are installed.")).toBeTruthy());
    expect(fixture.install).toHaveBeenCalledTimes(1);
  });
});
