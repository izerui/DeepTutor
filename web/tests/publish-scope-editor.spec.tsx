import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

const { fetchMock, t } = vi.hoisted(() => ({
  fetchMock: vi.fn(),
  // Stable like the real i18next ``t``: effects depend on it.
  t: (key: string, values?: Record<string, unknown>) =>
    key.replace(/\{\{(\w+)\}\}/g, (_: string, name: string) => String(values?.[name] ?? "")),
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t }) }));
vi.mock("@/lib/api", () => ({ apiFetch: fetchMock, apiUrl: (path: string) => path }));

import { PublishScopeEditor } from "@/components/publishing/PublishScopeEditor";

const described = (everyone: boolean, classes: { id: string; name: string }[]) => ({
  kind: "book",
  item_id: "bk1",
  published: true,
  scope: { everyone, users: [], classes, managed: true },
  audience_size: everyone ? 9 : 2,
  choices: { classes: [{ id: "c1", name: "Class A" }] },
  suggested_classes: ["c1"],
});
const reply = (status: number, body: unknown) => ({ ok: status < 300, status, json: async () => body });

beforeEach(() => fetchMock.mockReset());

it("asks before narrowing and retries with confirm", async () => {
  fetchMock
    .mockResolvedValueOnce(reply(200, described(true, [])))
    .mockResolvedValueOnce(reply(409, { detail: "x", needs_confirmation: true, affected: 7 }))
    .mockResolvedValueOnce(reply(200, described(false, [{ id: "c1", name: "Class A" }])));
  render(<PublishScopeEditor kind="book" itemId="bk1" />);
  fireEvent.click(await screen.findByLabelText("Everyone"));
  fireEvent.click(screen.getByLabelText("Class A"));
  fireEvent.click(screen.getByRole("button", { name: "Save who receives this" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("removes access for 7 people");
  fireEvent.click(screen.getByRole("button", { name: "Save anyway" }));
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("2 people receive this"));
  const body = JSON.parse(fetchMock.mock.calls[2][1].body);
  expect(body).toEqual({ everyone: false, users: [], classes: ["c1"], confirm: true });
});

it("shows nothing to someone who is not the publisher", async () => {
  fetchMock.mockResolvedValueOnce(reply(404, { detail: "Only the publisher can choose who receives this." }));
  const { container } = render(<PublishScopeEditor kind="book" itemId="bk1" />);
  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  await waitFor(() => expect(container).toBeEmptyDOMElement());
});
