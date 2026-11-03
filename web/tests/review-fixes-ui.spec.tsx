import { render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";

// Fixes from the 2026-11-03 review of batches 0–4.
const { comments, org, t } = vi.hoisted(() => ({
  comments: { writtenComments: vi.fn(), myComments: vi.fn(), writeComment: vi.fn(), deleteComment: vi.fn() },
  org: { getOrg: vi.fn() },
  t: (key: string, values?: Record<string, unknown>) =>
    key.replace(/\{\{(\w+)\}\}/g, (_: string, name: string) => String(values?.[name] ?? "")),
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t }) }));
vi.mock("@/lib/comments-api", () => comments);
vi.mock("@/lib/classes-api", () => ({ ...org }));

import { useOrgLists } from "@/components/classes/ClassOrg";
import { CommentEditor } from "@/components/comments/Comments";

it("uses comments fetched once by the caller instead of asking per point", () => {
  render(
    <CommentEditor kind="path_point" subject="tpl" target="kp1" studentId="u"
      preloaded={[{ id: "c", author: "tina", student_id: "u", kind: "path_point", ref: "tpl:kp1", text: "Hi",
        created_at: 1, updated_at: 1 }]} />,
  );
  expect(screen.getByRole("button", { name: "Edit comment" })).toBeTruthy();
  expect(comments.writtenComments).not.toHaveBeenCalled();
});

function Lists({ label }: { label: string }) {
  const [lists] = useOrgLists();
  return <p>{label}: {lists.departments.map((d) => d.name).join(",")}</p>;
}

it("shares one department list across the page", async () => {
  org.getOrg.mockResolvedValue({ departments: [{ id: "d1", name: "Physics" }], grades: [] });
  render(<><Lists label="filter" /><Lists label="panel" /></>);
  await waitFor(() => expect(screen.getByText("filter: Physics")).toBeTruthy());
  expect(screen.getByText("panel: Physics")).toBeTruthy();
});
