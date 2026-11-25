import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { OutputFileActions, supportsGeneratedOutputActions } from "@/components/files/OutputFileActions";

const mocks = vi.hoisted(() => ({ fetch: vi.fn() }));
vi.mock("@/shared/api/client", () => ({
  apiFetch: (...args: unknown[]) => mocks.fetch(...args),
  apiUrl: (path: string) => `http://api.test${path}`,
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (s: string, values?: Record<string, unknown>) =>
      s.replace(/\{\{(\w+)\}\}/g, (_, key: string) => String(values?.[key] ?? "")),
  }),
}));

const url = `/files/workspace-items/ws_${"a".repeat(32)}/wsi_${"b".repeat(32)}`;
const preview = {
  file_bytes: 1234,
  cache_bytes: 4096,
  cache_files: 3,
  fingerprint: "f".repeat(64),
};
const ok = (body: unknown) => ({ ok: true, json: async () => body });

beforeEach(() => {
  mocks.fetch.mockReset();
});

it("offers cleanup only for generated files at supported workspace locations", () => {
  const file = { url, generated: true, relative_path: "outputs/chat/math/turn_a/exec/chart.png" };
  expect(supportsGeneratedOutputActions(file)).toBe(true);
  expect(supportsGeneratedOutputActions({ ...file, generated: false })).toBe(false);
  expect(supportsGeneratedOutputActions({ ...file, relative_path: "documents/chart.png" })).toBe(false);
  expect(supportsGeneratedOutputActions({ ...file, url: "https://example.org/chart.png" })).toBe(false);
});

it("removes the reviewed workspace copy and keeps the published download", async () => {
  mocks.fetch.mockResolvedValueOnce(ok(preview)).mockResolvedValueOnce(ok({ removed: true }));
  render(<OutputFileActions url={`${url}?dt_workspace=course-one`} generatedFile />);
  fireEvent.click(screen.getByRole("button", { name: "Output files" }));
  fireEvent.click(await screen.findByRole("button", { name: "Remove workspace file" }));
  expect(mocks.fetch).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getAllByRole("button", { name: "Remove workspace file" })[1]);
  await screen.findByText("Workspace file removed. The chat attachment is still available.");
  expect(mocks.fetch).toHaveBeenCalledWith(`${url}/remove-generated-output?dt_workspace=course-one`,
    expect.objectContaining({ method: "POST", body: JSON.stringify({ fingerprint: preview.fingerprint }) }));
  expect(screen.getByRole("link", { name: "Download" })).toHaveAttribute("href", `http://api.test${url}?dt_workspace=course-one`);
  expect(screen.queryByRole("button", { name: "Remove workspace file" })).toBeNull();
});

it("offers Open and Download for any generated file", () => {
  render(<OutputFileActions url="/files/outputs/workspace/chat/video_lesson/t/artifacts/lesson.html" filename="lesson.html" />);
  const open = screen.getByRole("link", { name: "Open in new tab" });
  expect(open).toHaveAttribute("href", "http://api.test/files/outputs/workspace/chat/video_lesson/t/artifacts/lesson.html");
  expect(open).toHaveAttribute("target", "_blank");
  const download = screen.getByRole("link", { name: "Download" });
  expect(download).toHaveAttribute("download", "lesson.html");
  expect(download).toHaveAttribute("href", open.getAttribute("href"));
  expect(screen.queryByRole("button", { name: "Render files" })).toBeNull();
  expect(mocks.fetch).not.toHaveBeenCalled();
});

it("opens in the app when given a handler and can leave Open to the card", () => {
  const onOpen = vi.fn();
  const { rerender } = render(<OutputFileActions url={url} onOpen={onOpen} />);
  fireEvent.click(screen.getByRole("button", { name: "Open" }));
  expect(onOpen).toHaveBeenCalledOnce();
  rerender(<OutputFileActions url={url} showOpen={false} />);
  expect(screen.queryByRole("button", { name: "Open" })).toBeNull();
  expect(screen.getByRole("link", { name: "Download" })).toBeInTheDocument();
});

it("previews render cleanup without exposing server paths and requires confirmation", async () => {
  mocks.fetch.mockImplementation(async (path: string) =>
    path.endsWith("clean-render-cache")
      ? ok({ cache_files: 0, cache_bytes: 0, freed_bytes: 4096, fingerprint: "e".repeat(64) })
      : ok(preview),
  );
  render(<OutputFileActions url={url} renderFiles />);
  expect(mocks.fetch).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Render files" }));
  await screen.findByText("Render files: 3 files · 4.0 KB");
  expect(screen.queryByText(/Saved file path|Show in file manager/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Clean render files" }));
  const dialog = await screen.findByRole("alertdialog");
  expect(dialog).toHaveTextContent("Remove 3 render files and free 4.0 KB?");
  expect(mocks.fetch.mock.calls.some(([p]) => p.endsWith("clean-render-cache"))).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("alertdialog")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Clean render files" }));
  await screen.findByRole("alertdialog");
  fireEvent.click(screen.getAllByRole("button", { name: "Clean render files" })[1]);
  await screen.findByText("Freed 4.0 KB. Final output is kept.");
  expect(mocks.fetch).toHaveBeenCalledWith(
    `${url}/clean-render-cache`,
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ fingerprint: preview.fingerprint }),
    }),
  );
  expect(screen.getByRole("button", { name: "Clean render files" })).toBeDisabled();
});

it("shows cleanup errors without claiming that storage was freed", async () => {
  mocks.fetch.mockImplementation(async (path: string) =>
    path.endsWith("clean-render-cache")
      ? { ok: false, json: async () => ({ detail: "Render files changed. Review again." }) }
      : ok(preview),
  );
  render(<OutputFileActions url={url} renderFiles />);
  fireEvent.click(screen.getByRole("button", { name: "Render files" }));
  await screen.findByText("Render files: 3 files · 4.0 KB");
  fireEvent.click(screen.getByRole("button", { name: "Clean render files" }));
  await screen.findByRole("alertdialog");
  fireEvent.click(screen.getAllByRole("button", { name: "Clean render files" })[1]);
  expect(await screen.findByRole("alert")).toHaveTextContent("Render files changed.");
  expect(screen.queryByText(/Freed/)).toBeNull();
});

it("offers no cleanup for external or legacy URLs", () => {
  render(<OutputFileActions url="https://example.test/movie.mp4" renderFiles />);
  expect(screen.getByRole("link", { name: "Download" })).toHaveAttribute(
    "href",
    "https://example.test/movie.mp4",
  );
  expect(screen.queryByRole("button", { name: "Render files" })).toBeNull();
  expect(mocks.fetch).not.toHaveBeenCalled();
});

it("keeps the artifact's workspace scope and resets details for another file", async () => {
  mocks.fetch.mockResolvedValue(ok(preview));
  const { rerender } = render(<OutputFileActions url={`${url}?dt_workspace=course-one`} renderFiles />);
  fireEvent.click(screen.getByRole("button", { name: "Render files" }));
  await screen.findByText("Render files: 3 files · 4.0 KB");
  expect(mocks.fetch).toHaveBeenCalledWith(`${url}/render-storage?dt_workspace=course-one`, undefined);
  const other = `/files/workspace-items/ws_${"c".repeat(32)}/wsi_${"d".repeat(32)}`;
  rerender(<OutputFileActions url={other} renderFiles />);
  await waitFor(() => expect(screen.queryByText(/Render files: 3/)).toBeNull());
  expect(screen.getByRole("button", { name: "Render files" })).toHaveAttribute("aria-expanded", "false");
});
