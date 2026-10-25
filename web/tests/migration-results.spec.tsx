import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createInstance } from "i18next";
import { I18nextProvider } from "react-i18next";
import { MigrationResultsNotice } from "@/features/multi-user/components/MigrationResultsNotice";
import { fetchMigrationResults } from "@/features/multi-user/api";
import { apiFetch } from "@/lib/api";
import type { MigrationItem } from "@/features/multi-user/types";
import DataMigrationSettingsSection from "@/features/settings/sections/DataMigrationSettingsSection";
import { visibleSettingsPages } from "@/features/settings/navigation/settings-pages";

const auth = vi.hoisted(() => ({
  loading: false, statusAvailable: true, isAdmin: true, enabled: true, authenticated: true,
}));
vi.mock("@/hooks/useAuthStatus", () => ({ useAuthStatus: () => auth }));
vi.mock("@/hooks/useChatWorkspaces", () => ({ useChatWorkspaces: () => ({ workspaces: [] }) }));
vi.mock("@/lib/workspace-scope", () => ({ activeWorkspaceId: () => "" }));

vi.mock("@/lib/api", () => ({
  apiUrl: (path: string) => `/kaoyan${path}`,
  apiFetch: vi.fn(),
}));

afterEach(cleanup);
beforeEach(() => {
  Object.assign(auth, { loading: false, statusAvailable: true, isAdmin: true, enabled: true, authenticated: true });
});

async function show(items: MigrationItem[] = []) {
  vi.mocked(apiFetch).mockResolvedValue(Response.json({ items }));
  const i18n = createInstance();
  await i18n.init({ lng: "en", fallbackLng: "en", resources: {}, interpolation: { escapeValue: false } });
  return render(<I18nextProvider i18n={i18n}><MigrationResultsNotice /></I18nextProvider>);
}

it("renders all migration kinds and safely falls back for future and prototype keys", async () => {
  await show([
    { kind: "kb_made_public", key: "kb:1", data: { name: "<Study>", users: ["alice", "bob"] }, created_at: "" },
    { kind: "connector_grant_disabled", key: "Connector", data: { users: ["alice"] }, created_at: "" },
    { kind: "partner_copy_adopted", key: "Copy", data: { partner_id: "partner-1" }, created_at: "" },
    ...["book_made_public", "constructor", "__proto__", "toString"].map(kind => ({ kind, key: "future", data: {}, created_at: "" })),
  ]);
  await screen.findByRole("heading", { name: "Migration results" });
  expect(screen.getByText('Knowledge base “<Study>” is now public because it was granted to: alice, bob')).toBeVisible();
  expect(screen.getByText("Connector “Connector” stays private, so its grant was disabled. Affected accounts: alice")).toBeVisible();
  expect(screen.getByText(/Partner partner-1 kept its own copy of “Copy”/)).toBeVisible();
  for (const kind of ["book_made_public", "constructor", "__proto__", "toString"])
    expect(screen.getByText(`${kind}: future`)).toBeVisible();
  expect(apiFetch).toHaveBeenCalledWith("/kaoyan/api/multi-user/admin/migration-results");
});

it("renders nothing for an empty deployment", async () => {
  const view = await show();
  await act(async () => {});
  expect(view.container).toBeEmptyDOMElement();
});

it.each([403, 500])("renders nothing when the endpoint returns %s", async status => {
  vi.mocked(apiFetch).mockResolvedValue(Response.json({ detail: "Denied" }, { status }));
  const view = render(<MigrationResultsNotice />);
  await act(async () => {});
  expect(view.container).toBeEmptyDOMElement();
});

it("ignores a response that arrives after unmount", async () => {
  let resolve!: (value: Response) => void;
  vi.mocked(apiFetch).mockReturnValue(new Promise(done => { resolve = done; }));
  const view = render(<MigrationResultsNotice />);
  view.unmount();
  await act(async () => resolve(Response.json({ items: [{ kind: "future", key: "late", data: {}, created_at: "" }] })));
  expect(screen.queryByText("future: late")).not.toBeInTheDocument();
});

it("treats a missing items array as empty and preserves API errors", async () => {
  vi.mocked(apiFetch).mockResolvedValueOnce(Response.json({}));
  expect(await fetchMigrationResults()).toEqual([]);
  vi.mocked(apiFetch).mockResolvedValueOnce(Response.json({ detail: "Admin only" }, { status: 403 }));
  await expect(fetchMigrationResults()).rejects.toThrow("Admin only");
});

it.each([
  { label: "ordinary user", isAdmin: false },
  { label: "student", isAdmin: false },
  { label: "loading administrator", loading: true },
  { label: "unavailable identity", statusAvailable: false },
  { label: "unauthenticated identity", authenticated: false },
])("does not fetch migration results for $label", async state => {
  Object.assign(auth, state);
  const view = render(<MigrationResultsNotice />);
  await act(async () => {});
  expect(apiFetch).not.toHaveBeenCalled();
  expect(view.container).toBeEmptyDOMElement();
});

it("waits for confirmed admin access and hides results when access is lost", async () => {
  auth.loading = true;
  vi.mocked(apiFetch).mockResolvedValue(Response.json({ items: [
    { kind: "future", key: "admin-only", data: {}, created_at: "" },
  ] }));
  const view = render(<MigrationResultsNotice />);
  await act(async () => {});
  expect(apiFetch).not.toHaveBeenCalled();
  auth.loading = false;
  view.rerender(<MigrationResultsNotice />);
  await screen.findByText("future: admin-only");
  auth.isAdmin = false;
  view.rerender(<MigrationResultsNotice />);
  expect(view.container).toBeEmptyDOMElement();
  expect(apiFetch).toHaveBeenCalledTimes(1);
});

it("retains personal data migration while making no admin migration request", async () => {
  auth.isAdmin = false;
  const pages = visibleSettingsPages({
    resolved: true, hideAdminOnly: true, showLearnerOnly: false, showGuardianOnly: true,
  });
  expect(pages.some(page => page.key === "data-migration")).toBe(true);
  vi.mocked(apiFetch).mockImplementation(async url => Response.json(
    String(url).includes("/discover?")
      ? { features: [], historical: [], session_backend: "sqlite" }
      : { operations: [] },
  ));
  const i18n = createInstance();
  await i18n.init({ lng: "en", resources: {} });
  render(<I18nextProvider i18n={i18n}><DataMigrationSettingsSection /></I18nextProvider>);
  await act(async () => {});
  expect(screen.getByRole("heading", { name: "Data migration" })).toBeVisible();
  expect(screen.getByRole("combobox", { name: "Source workspace" })).toBeVisible();
  expect(apiFetch).toHaveBeenCalledWith(expect.stringContaining("/discover?"), undefined);
  expect(vi.mocked(apiFetch).mock.calls.some(([url]) => String(url).includes("/admin/migration-results"))).toBe(false);
});
