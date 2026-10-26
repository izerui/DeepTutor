import test from "node:test";
import assert from "node:assert/strict";

import type { MasteryTopic } from "../lib/learning-api";
import type {
  ChangeSuggestion,
  KpDecision,
  PublishPreview,
  TeacherTemplate,
} from "../lib/teaching-api";
import {
  canSubmitPublish,
  decisionValue,
  defaultKpDecisions,
  duplicateContinuations,
  isTeacherInstance,
  parseDecision,
  splitAssignedTopics,
  templateForTopic,
  withMasteryTemplate,
} from "../components/space/learning/teaching";

type TopicLike = Pick<MasteryTopic, "path_id" | "origin" | "content_workspace_id">;

const own: TopicLike = { path_id: "topic_a", origin: null, content_workspace_id: "" };
const assigned: TopicLike = {
  path_id: "assigned_tpl_1",
  content_workspace_id: "",
  origin: {
    kind: "teacher_template",
    template_id: "tpl_1",
    revision: 2,
    teacher_name: "tina",
    update_notice: null,
    materials_available: null,
  },
};

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

test("assigned topics are told apart by their origin", () => {
  assert.equal(isTeacherInstance(assigned), true);
  assert.equal(isTeacherInstance(own), false);
  const split = splitAssignedTopics([own, assigned]);
  assert.deepEqual(split.assigned.map((topic) => topic.path_id), ["assigned_tpl_1"]);
  assert.deepEqual(split.own.map((topic) => topic.path_id), ["topic_a"]);
});

test("a topic finds its live template in the same workspace", () => {
  assert.equal(templateForTopic([template()], own)?.id, "tpl_1");
  assert.equal(templateForTopic([template({ status: "archived" })], own), undefined);
  assert.equal(templateForTopic([template({ draft_workspace_id: "ws1" })], own), undefined);
});

test("change decisions survive a round trip through a select value", () => {
  assert.equal(decisionValue(undefined), "unchanged");
  assert.equal(decisionValue({ kind: "added" }), "added");
  assert.equal(decisionValue({ kind: "reworded", from_id: "kp_a" }), "reworded:kp_a");
  assert.deepEqual(parseDecision("redefined:kp_b"), { kind: "redefined", from_id: "kp_b" });
  assert.deepEqual(parseDecision("unchanged"), { kind: "unchanged" });
});

test("model suggestions become the default decisions", () => {
  const items: ChangeSuggestion[] = [
    {
      kp_id: "a",
      name: "Limits",
      status: "kept",
      suggested: "unchanged",
      from_id: "",
      from_name: "",
      reason: "",
    },
    {
      kp_id: "b2",
      name: "Derivative rules",
      status: "new",
      suggested: "reworded",
      from_id: "b",
      from_name: "Derivatives",
      reason: "",
    },
  ];
  assert.deepEqual(defaultKpDecisions(items), {
    a: { kind: "unchanged" },
    b2: { kind: "reworded", from_id: "b" },
  });
});

test("publishing waits for a clean preview, an idle dialog and unique continuations", () => {
  const preview = { can_publish: true } as PublishPreview;
  assert.equal(canSubmitPublish(preview, {}, false), true);
  assert.equal(canSubmitPublish(preview, {}, true), false);
  assert.equal(canSubmitPublish({ can_publish: false } as PublishPreview, {}, false), false);
  assert.equal(canSubmitPublish(null, {}, false), false);
  const reused: Record<string, KpDecision> = {
    x: { kind: "reworded", from_id: "b" },
    y: { kind: "redefined", from_id: "b" },
  };
  assert.deepEqual(duplicateContinuations(reused), ["b"]);
  assert.equal(canSubmitPublish(preview, reused, false), false);
});

test("Study-page source links carry the teacher path (R9)", () => {
  const page = "/api/knowledge-bases/kb%3Aabc/source-page?source_path=a.pdf&page=2";
  assert.equal(withMasteryTemplate(page, ""), page);
  assert.equal(
    withMasteryTemplate(page, "tpl_1"),
    "/api/knowledge-bases/kb%3Aabc/source-page?source_path=a.pdf&page=2&mastery_template=tpl_1",
  );
  assert.equal(
    withMasteryTemplate("/api/knowledge-bases/kb%3Aabc/files/a.md", "tpl 2"),
    "/api/knowledge-bases/kb%3Aabc/files/a.md?mastery_template=tpl%202",
  );
});
