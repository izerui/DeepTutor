import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createRef } from "react";
import { beforeEach, expect, it, vi } from "vitest";

import type { MasteryTopic } from "@/lib/learning-api";
import type { PublishPreview, TeacherTemplate } from "@/lib/teaching-api";

const api = vi.hoisted(() => ({
  registerTeacherTemplate: vi.fn(),
  previewPublish: vi.fn(),
  publishTemplate: vi.fn(),
  listAssignments: vi.fn(),
  assignStudents: vi.fn(),
  revokeAssignment: vi.fn(),
  archiveTemplate: vi.fn(),
  deleteTemplate: vi.fn(),
  searchStudents: vi.fn(),
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      key.replace(/{{(\w+)}}/g, (_match, name: string) => String(values?.[name] ?? "")),
  }),
}));

vi.mock("@/lib/teaching-api", async (original) => ({
  ...(await original<typeof import("@/lib/teaching-api")>()),
  ...api,
}));

import { TeachingApiError } from "@/lib/teaching-api";
import { PublishTemplateDialog } from "@/components/space/learning/PublishTemplateDialog";
import { TemplateAssignmentsDialog } from "@/components/space/learning/TemplateAssignmentsDialog";
import {
  TeacherCardActions,
  UnlistedTeacherTemplates,
} from "@/components/space/learning/TeacherCardActions";

function template(overrides: Partial<TeacherTemplate> = {}): TeacherTemplate {
  return {
    id: "tpl_1",
    owner_user_id: "u_tina",
    owner_name: "tina",
    is_owner: true,
    title: "Waves",
    description: "",
    status: "draft",
    current_revision: 0,
    draft_path_id: "topic_waves",
    draft_workspace_id: "",
    assigned_count: 0,
    materials_unavailable: [],
    created_at: 0,
    updated_at: 0,
    ...overrides,
  };
}

function topic(pathId = "topic_waves"): MasteryTopic {
  return {
    path_id: pathId,
    name: `Path ${pathId}`,
    content_workspace_id: "",
    sources: [
      { id: "s_book", kind: "book", source_id: "book:bk_tom", label: "Tom's notes" },
      { id: "s_notes", kind: "notebook", source_id: "nb", label: "My notes" },
    ],
  } as unknown as MasteryTopic;
}

function preview(overrides: Partial<PublishPreview> = {}): PublishPreview {
  return {
    template: template(),
    draft_revision: 3,
    outline_issues: [],
    source_issues: [],
    private_kbs: [
      { kb_id: "kb_1", name: "Answers", default: "make_public", via: "", via_name: "" },
    ],
    private_books: [{ book_id: "bk_1", name: "Waves book", default: "make_public" }],
    books: [],
    changes: { first_publish: true, items: [], removed: [] },
    module_count: 1,
    knowledge_point_count: 2,
    affected_students: 0,
    can_publish: true,
    ...overrides,
  };
}

beforeEach(() => {
  for (const mock of Object.values(api)) mock.mockReset();
  api.registerTeacherTemplate.mockResolvedValue(template());
  api.listAssignments.mockResolvedValue([]);
  api.searchStudents.mockResolvedValue([{ id: "u_sara", username: "sara", avatar: "" }]);
});

it("registers a first publish, defaults private materials to public and sends the choices", async () => {
  api.previewPublish.mockResolvedValue(preview());
  api.publishTemplate.mockResolvedValue({
    template: template({ current_revision: 1, status: "published" }),
    revision: 1,
    affected_students: 1,
    warnings: [],
  });
  const published = vi.fn();
  render(
    <PublishTemplateDialog
      topic={topic()}
      returnFocusRef={createRef()}
      onClose={vi.fn()}
      onPublished={published}
    />,
  );
  const book = await screen.findByRole("group", { name: "Waves book" });
  expect(within(book).getByRole("radio", { name: "Make the book public" })).toBeChecked();
  const kb = screen.getByRole("group", { name: "Answers" });
  fireEvent.click(within(kb).getByRole("radio", { name: "Readable through this path only" }));
  fireEvent.click(await screen.findByRole("checkbox", { name: "sara" }));
  expect(api.registerTeacherTemplate).toHaveBeenCalledWith("topic_waves", "");
  expect(screen.queryByText("What changed")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Publish" }));
  await waitFor(() => expect(published).toHaveBeenCalled());
  expect(api.publishTemplate).toHaveBeenCalledWith("tpl_1", {
    draft_revision: 3,
    kp_decisions: {},
    kb_choices: { kb_1: "reference_only" },
    book_choices: { bk_1: "make_public" },
    student_ids: ["u_sara"],
  });
});

it("localizes blocking source issues by code and keeps publishing disabled", async () => {
  api.previewPublish.mockResolvedValue(
    preview({
      can_publish: false,
      private_kbs: [],
      private_books: [],
      source_issues: [
        { source_id: "s_notes", label: "My notes", code: "personal_source", message: "EN text" },
        { source_id: "s_book", label: "Tom's notes", code: "not_shareable", message: "EN text" },
      ],
    }),
  );
  render(
    <PublishTemplateDialog
      topic={topic()}
      template={template()}
      returnFocusRef={createRef()}
      onClose={vi.fn()}
      onPublished={vi.fn()}
    />,
  );
  expect(await screen.findByText(/My notes is a personal source/)).toBeVisible();
  expect(screen.getByText("Tom's notes is someone else's private book, or cannot be shared.")).toBeVisible();
  expect(screen.queryByText("EN text")).toBeNull();
  expect(screen.getByRole("button", { name: "Publish" })).toBeDisabled();
  expect(api.registerTeacherTemplate).not.toHaveBeenCalled();
});

it("drops a slow preview of a topic the dialog no longer shows", async () => {
  let resolveFirst!: (value: PublishPreview) => void;
  api.previewPublish
    .mockReturnValueOnce(new Promise((resolve) => (resolveFirst = resolve)))
    .mockResolvedValueOnce(preview({ private_books: [] }));
  const props = { returnFocusRef: createRef<HTMLElement>(), onClose: vi.fn(), onPublished: vi.fn() };
  const { rerender } = render(
    <PublishTemplateDialog {...props} topic={topic("a")} template={template({ id: "tpl_a" })} />,
  );
  rerender(<PublishTemplateDialog {...props} topic={topic("b")} template={template({ id: "tpl_b" })} />);
  await screen.findByRole("group", { name: "Answers" });
  await act(async () => resolveFirst(preview()));
  expect(screen.queryByRole("group", { name: "Waves book" })).toBeNull();
});

it("shows an update's change list with per-point decisions and the affected students", async () => {
  api.previewPublish.mockResolvedValue(
    preview({
      private_kbs: [],
      private_books: [],
      affected_students: 2,
      changes: {
        first_publish: false,
        items: [
          { kp_id: "kp_l", name: "Limits", status: "kept", suggested: "unchanged", from_id: "", from_name: "", reason: "" },
          { kp_id: "kp_r", name: "Derivative rules", status: "new", suggested: "added", from_id: "", from_name: "", reason: "" },
        ],
        removed: [{ kp_id: "kp_d", name: "Derivatives" }],
      },
    }),
  );
  api.listAssignments.mockResolvedValue([
    { student_user_id: "u_sara", status: "active" },
    { student_user_id: "u_sam", status: "revoked" },
  ]);
  api.publishTemplate.mockResolvedValue({
    template: template({ current_revision: 2 }),
    revision: 2,
    affected_students: 2,
    warnings: [{ step: "assignments", message: "store busy" }],
  });
  const published = vi.fn();
  render(
    <PublishTemplateDialog
      topic={topic()}
      template={template({ current_revision: 1, status: "published" })}
      returnFocusRef={createRef()}
      onClose={vi.fn()}
      onPublished={published}
    />,
  );
  expect(await screen.findByRole("heading", { name: "Publish update" })).toBeVisible();
  expect(screen.getByText("2 students will get this update the next time they study")).toBeVisible();
  expect(screen.getByText("Removed: Derivatives")).toBeVisible();
  // The already-assigned student is not offered again; the revoked one is.
  await waitFor(() => expect(api.searchStudents).toHaveBeenCalled());
  fireEvent.change(screen.getByRole("combobox", { name: "Derivative rules" }), {
    target: { value: "reworded:kp_d" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Publish" }));
  // Publication succeeded with a post-version warning: said explicitly, then closed by the teacher.
  expect(await screen.findByText("Published, but some steps need attention")).toBeVisible();
  expect(screen.getByText("Student assignments did not complete.")).toBeVisible();
  expect(published).not.toHaveBeenCalled();
  expect(api.publishTemplate.mock.calls[0][1].kp_decisions).toEqual({
    kp_l: { kind: "unchanged" },
    kp_r: { kind: "reworded", from_id: "kp_d" },
  });
  fireEvent.click(screen.getAllByRole("button", { name: "Close" }).at(-1)!);
  expect(published).toHaveBeenCalled();
});

it("reports a publish that stopped at a step, localized, with what already completed", async () => {
  api.previewPublish.mockResolvedValue(preview());
  api.publishTemplate.mockRejectedValue(
    new TeachingApiError("{}", 422, {
      outline_issues: [],
      source_issues: [{ source_id: "", label: "make_public:kb:kb_1", code: "publish_step_failed", message: "EN" }],
      failed_step: "make_public:kb:kb_1",
      completed_steps: ["documents"],
    }),
  );
  render(
    <PublishTemplateDialog
      topic={topic()}
      template={template()}
      returnFocusRef={createRef()}
      onClose={vi.fn()}
      onPublished={vi.fn()}
    />,
  );
  await screen.findByRole("group", { name: "Answers" });
  fireEvent.click(screen.getByRole("button", { name: "Publish" }));
  expect(await screen.findByText(/It stopped at: Making the knowledge base Answers public/)).toBeVisible();
  expect(screen.getByText("Already done before it stopped: Registering the cited documents")).toBeVisible();
  expect(screen.getByRole("button", { name: "Publish" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Check again" })).toBeEnabled();
});

it("manages assignments: revoke, add and archive behind a confirmation", async () => {
  api.listAssignments.mockResolvedValue([
    { student_user_id: "u_sara", username: "sara", status: "active", applied_revision: 1 },
  ]);
  api.searchStudents.mockResolvedValue([
    { id: "u_sara", username: "sara", avatar: "" },
    { id: "u_sam", username: "sam", avatar: "" },
  ]);
  api.revokeAssignment.mockResolvedValue({ ok: true });
  api.assignStudents.mockResolvedValue([]);
  api.archiveTemplate.mockResolvedValue(template({ status: "archived" }));
  const changed = vi.fn();
  const close = vi.fn();
  render(
    <TemplateAssignmentsDialog
      template={template({ status: "published", current_revision: 1, is_owner: false })}
      returnFocusRef={createRef()}
      onClose={close}
      onChanged={changed}
    />,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Remove access for sara" }));
  await waitFor(() => expect(api.revokeAssignment).toHaveBeenCalledWith("tpl_1", "u_sara"));
  // Sara still has the path; only Sam is offered.
  expect(await screen.findByRole("checkbox", { name: "sam" })).toBeVisible();
  expect(screen.queryByRole("checkbox", { name: "sara" })).toBeNull();
  fireEvent.click(screen.getByRole("checkbox", { name: "sam" }));
  fireEvent.click(screen.getByRole("button", { name: "Assign students" }));
  await waitFor(() => expect(api.assignStudents).toHaveBeenCalledWith("tpl_1", ["u_sam"]));
  fireEvent.click(screen.getByRole("button", { name: "Archive" }));
  expect(api.archiveTemplate).not.toHaveBeenCalled();
  const confirm = await screen.findByRole("alertdialog");
  fireEvent.click(within(confirm).getByRole("button", { name: "Archive" }));
  await waitFor(() => expect(close).toHaveBeenCalled());
  expect(api.archiveTemplate).toHaveBeenCalledWith("tpl_1");
  expect(changed).toHaveBeenCalledTimes(3);
});

it("offers publishing only on an own card; other teachers' paths are manage-only with a reason", () => {
  const onManage = vi.fn();
  render(
    <>
      <TeacherCardActions
        topic={topic()}
        template={template({ current_revision: 2, assigned_count: 3, status: "published" })}
        onPublish={vi.fn()}
        onManage={onManage}
      />
      <UnlistedTeacherTemplates
        templates={[template({ id: "tpl_tom", is_owner: false, owner_name: "tom", title: "Optics" })]}
        onManage={onManage}
      />
    </>,
  );
  expect(screen.getByText(/Published v2/)).toBeVisible();
  expect(screen.getAllByRole("button", { name: "Publish update" })).toHaveLength(1);
  const others = screen.getByRole("region", { name: "Other teacher paths" });
  expect(within(others).queryByRole("button", { name: /Publish/ })).toBeNull();
  expect(within(others).getByText(/Only tom can edit and publish this path/)).toBeVisible();
  fireEvent.click(within(others).getByRole("button", { name: "Manage assignments" }));
  expect(onManage.mock.calls[0][0].id).toBe("tpl_tom");
});
