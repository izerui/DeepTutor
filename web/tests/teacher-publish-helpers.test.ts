import test from "node:test";
import assert from "node:assert/strict";

import { TeachingApiError, type TeacherTemplate } from "../lib/teaching-api";
import {
  publishBlocked,
  publishStepMessage,
  sourceIssueMessage,
  teachingErrorMessage,
  unlistedTemplates,
  unpublishableReason,
} from "../components/space/learning/teaching";

function template(overrides: Partial<TeacherTemplate> = {}): TeacherTemplate {
  return {
    id: "tpl_1",
    owner_user_id: "u_tina",
    owner_name: "tina",
    is_owner: true,
    title: "Calculus",
    description: "",
    status: "published",
    current_revision: 1,
    draft_path_id: "topic_a",
    draft_workspace_id: "",
    assigned_count: 2,
    materials_unavailable: [],
    created_at: 0,
    updated_at: 0,
    ...overrides,
  };
}

const issue = (code: string, source_id = "s1") => ({
  source_id,
  label: "Waves",
  code,
  message: "English server text",
});

test("source issues are localized by code, never by the server's English text", () => {
  const codes = [
    "personal_source",
    "missing_kb",
    "connector",
    "not_shareable",
    "not_indexed",
    "missing_document",
    "missing_book",
    "book_kb_blocked",
    "workspace_unavailable",
  ];
  const keys = codes.map((code) => sourceIssueMessage(issue(code)).key);
  assert.equal(new Set(keys).size, codes.length);
  for (const key of keys) {
    assert.ok(key.includes("{{label}}"));
    assert.ok(!key.includes("English server text"));
  }
  assert.deepEqual(sourceIssueMessage(issue("missing_book")).values, { label: "Waves" });
  // A foreign private book is not described as a knowledge base.
  assert.notEqual(
    sourceIssueMessage(issue("not_shareable", "b1"), { b1: "book" }).key,
    sourceIssueMessage(issue("not_shareable", "k1"), { k1: "knowledge_base" }).key,
  );
  assert.equal(sourceIssueMessage(issue("something_new")).key, "{{label}} cannot be shared with students.");
  assert.equal(
    sourceIssueMessage(issue("publish_step_failed")).key,
    "Publishing stopped; no new version was published.",
  );
});

test("publish steps name the material they acted on", () => {
  assert.deepEqual(publishStepMessage("make_public:kb:kb_1", { kb_1: "Answers" }), {
    key: "Making the knowledge base {{name}} public",
    values: { name: "Answers" },
  });
  assert.deepEqual(publishStepMessage("share:book:bk_1", {}).values, { name: "bk_1" });
  assert.equal(publishStepMessage("make_public:book:bk_1").key, "Making the book {{name}} public");
  assert.equal(publishStepMessage("documents").key, "Registering the cited documents");
  assert.equal(publishStepMessage("assignments").key, "Student assignments");
  assert.equal(publishStepMessage("book_references").key, "Book access for students");
  assert.equal(
    publishStepMessage("knowledge_base_references").key,
    "Knowledge base access for students",
  );
  assert.deepEqual(publishStepMessage("later_step"), {
    key: "Step {{step}}",
    values: { step: "later_step" },
  });
});

test("only a structured 422 is read as a blocked publish", () => {
  const detail = {
    outline_issues: [],
    source_issues: [issue("publish_step_failed")],
    failed_step: "make_public:kb:kb_1",
    completed_steps: ["documents"],
  };
  assert.equal(publishBlocked(new TeachingApiError("x", 422, detail))?.failed_step, "make_public:kb:kb_1");
  assert.equal(publishBlocked(new TeachingApiError("x", 422, "Only active student accounts")), null);
  assert.equal(publishBlocked(new TeachingApiError("x", 409, detail)), null);
  assert.equal(publishBlocked(new Error("x")), null);
});

test("other failures are localized by status", () => {
  assert.equal(
    teachingErrorMessage(new TeachingApiError("English", 403, "English")).key,
    "Only the teacher who owns this path can publish it.",
  );
  assert.equal(
    teachingErrorMessage(new TeachingApiError("English", 422, "English")).key,
    "Only active student accounts can be assigned.",
  );
  assert.equal(teachingErrorMessage(new TypeError("fetch failed")).key, "Update failed");
  assert.equal(teachingErrorMessage(new TeachingApiError("x", 500, "x")).key, "Update failed");
});

test("templates without an own card are listed with the reason they cannot be published", () => {
  const topics = [{ path_id: "topic_a", content_workspace_id: "" }];
  const own = template();
  const foreign = template({ id: "tpl_2", is_owner: false, owner_name: "tom" });
  const transferred = template({ id: "tpl_3", draft_path_id: "", draft_workspace_id: "" });
  const draftGone = template({ id: "tpl_4", draft_path_id: "topic_gone" });
  const archived = template({ id: "tpl_5", is_owner: false, status: "archived" });
  assert.deepEqual(
    unlistedTemplates([own, foreign, transferred, draftGone, archived], topics).map((row) => row.id),
    ["tpl_2", "tpl_3", "tpl_4"],
  );
  // A foreign template whose draft id happens to match an own topic is still foreign.
  assert.deepEqual(
    unlistedTemplates([template({ id: "tpl_6", is_owner: false })], topics).map((row) => row.id),
    ["tpl_6"],
  );
  assert.deepEqual(unpublishableReason(foreign).values, { owner: "tom" });
  assert.match(unpublishableReason(transferred).key, /account was removed/);
  assert.match(unpublishableReason(draftGone).key, /not in your library/);
});
