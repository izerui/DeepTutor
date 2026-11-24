import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { CreationDraftCard } from "@/components/chat/home/CreationDraftCard";
import type { CreationDraftData } from "@/lib/creation-draft";
import { CREATIONS_SAVED_EVENT } from "@/lib/creations";

const api = vi.hoisted(() => ({ fetch: vi.fn() }));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) =>
      options?.name ? `${key}:${String(options.name)}` : key,
  }),
}));
vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));
vi.mock("next/dynamic", () => ({
  default: () =>
    function DraftMarkdown({ content }: { content: string }) {
      return <div data-testid="draft-body">{content}</div>;
    },
}));
// Sub-path deployments: every request must be built by apiUrl.
vi.mock("@/lib/api", () => ({
  apiFetch: api.fetch,
  apiUrl: (path: string) => `/kaoyan${path}`,
}));
vi.mock("@/lib/skills-api", () => ({ invalidateSkillsCache: vi.fn() }));
vi.mock("@/lib/personas-api", () => ({ invalidatePersonasCache: vi.fn() }));

const DRAFT: CreationDraftData = {
  draft_id: "a".repeat(32),
  owner_id: "u-1",
  kind: "skill",
  session_id: "s-1",
  name: "limit-helper",
  description: "Explains limits.",
  content: "# Limits\nStep one.",
  tags: ["math"],
  references: [],
  status: "pending",
  created_name: "",
  replaced: false,
  created_at: "",
  approved_at: null,
  version: 1,
};

function respond(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status }));
}

beforeEach(() => {
  api.fetch.mockReset();
});

afterEach(() => {
  cleanup();
});

it("catches up with the server state and approves through apiUrl", async () => {
  const saved = vi.fn();
  window.addEventListener(CREATIONS_SAVED_EVENT, saved);
  api.fetch.mockImplementation((url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      return respond(200, {
        draft: { ...DRAFT, status: "created", created_name: "limit-helper" },
        saved_name: "limit-helper",
        replaced: false,
        already_created: false,
        href: "/space/skills",
      });
    }
    return respond(200, { draft: DRAFT, approval: "create" });
  });

  render(<CreationDraftCard data={DRAFT} />);
  await waitFor(() =>
    expect(api.fetch).toHaveBeenCalledWith(
      `/kaoyan/api/creations/drafts/${DRAFT.draft_id}`,
      expect.anything(),
    ),
  );

  fireEvent.click(screen.getByRole("button", { name: "composer.create.draft.approveCreate" }));
  await waitFor(() =>
    expect(screen.getByText("composer.create.draft.savedAs:limit-helper")).toBeInTheDocument(),
  );
  const [url, init] = api.fetch.mock.calls.find(([, options]) => options?.method === "POST")!;
  expect(url).toBe(`/kaoyan/api/creations/drafts/${DRAFT.draft_id}/approve`);
  expect(JSON.parse(String(init.body))).toEqual({
    name: "limit-helper",
    description: "Explains limits.",
    content: "# Limits\nStep one.",
    tags: ["math"],
  });
  expect(screen.getByRole("link", { name: "composer.create.manageSkills" })).toHaveAttribute(
    "href",
    "/space/skills",
  );
  expect(saved).toHaveBeenCalledTimes(1);
  window.removeEventListener(CREATIONS_SAVED_EVENT, saved);
});

it("reports a name conflict without claiming the draft was saved", async () => {
  api.fetch.mockImplementation((_url: string, init?: RequestInit) =>
    init?.method === "POST"
      ? respond(409, { detail: "exists" })
      : respond(200, { draft: DRAFT, approval: "create" }),
  );

  render(<CreationDraftCard data={DRAFT} />);
  fireEvent.click(screen.getByRole("button", { name: "composer.create.draft.approveCreate" }));
  await waitFor(() =>
    expect(screen.getByText("composer.create.draft.nameConflict:limit-helper")).toBeInTheDocument(),
  );
  expect(screen.getByText("composer.create.draft.notSavedYet")).toBeInTheDocument();
});

it("shows an already approved draft as saved after a reload", async () => {
  api.fetch.mockImplementation(() =>
    respond(200, {
      draft: { ...DRAFT, status: "created", created_name: "limit-helper", replaced: true },
      approval: "done",
    }),
  );

  render(<CreationDraftCard data={DRAFT} />);
  await waitFor(() =>
    expect(screen.getByText("composer.create.draft.updatedAs:limit-helper")).toBeInTheDocument(),
  );
  expect(
    screen.queryByRole("button", { name: "composer.create.draft.approveCreate" }),
  ).not.toBeInTheDocument();
});

it("uses no native title tooltips", () => {
  api.fetch.mockImplementation(() => respond(200, { draft: DRAFT, approval: "create" }));
  const { container } = render(<CreationDraftCard data={DRAFT} />);
  expect(container.querySelector("[title]")).toBeNull();
});
