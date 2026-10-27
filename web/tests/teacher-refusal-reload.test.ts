/**
 * B2-16: teacher-path refusal reasons must survive a page reload.
 *
 * Two scenarios:
 * 1. The turn was refused before the user message was saved (no
 *    orphanedFailedTurn). The failed submission record in local storage
 *    must carry `failureCode` so the "Not sent" line shows the localized
 *    teacher-path refusal instead of the generic label.
 * 2. The turn was refused after the user message was saved (orphanedFailedTurn
 *    with `failure_code`). The rendering must use `teacherTurnErrorMessage`
 *    to produce a localized string, not display the raw English error.
 */

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const source = (relative: string) =>
  fs.readFileSync(path.resolve(process.cwd(), relative), "utf8");

test("FailedSubmissionRecord carries an optional failureCode field", () => {
  const src = source("lib/failed-submissions.ts");
  assert.match(src, /failureCode\?:\s*string/);
});

test("annotateFailureCode is exported from failed-submissions", () => {
  const src = source("lib/failed-submissions.ts");
  assert.match(src, /export function annotateFailureCode/);
});

test("ChatStateAdapter imports annotateFailureCode and calls it on terminal error", () => {
  const adapter = source("features/chat/ChatStateAdapter.tsx");
  assert.match(adapter, /annotateFailureCode/);
  // Annotation must happen before the pendingSubmissionIds ref is cleared.
  const annotatePos = adapter.indexOf("annotateFailureCode(");
  const deletePos = adapter.indexOf(
    "pendingSubmissionIdsRef.current.delete(effectiveKey)",
    annotatePos > 0 ? annotatePos : 0,
  );
  assert.ok(
    annotatePos > 0 && deletePos > annotatePos,
    "annotateFailureCode must be called before the pending ref is cleared",
  );
});

test("restoredFailedMessage carries failureCode from the record", () => {
  const adapter = source("features/chat/ChatStateAdapter.tsx");
  // The restoredFailedMessage function must propagate the code.
  assert.match(adapter, /record\.failureCode/);
  assert.match(adapter, /failureCode.*:.*record\.failureCode/);
});

test("MessageItem type includes failureCode", () => {
  const adapter = source("features/chat/ChatStateAdapter.tsx");
  // The exported interface should have the field.
  const messageItemBlock = adapter.slice(
    adapter.indexOf("export interface MessageItem"),
    adapter.indexOf("}", adapter.indexOf("export interface MessageItem")) + 1,
  );
  assert.match(messageItemBlock, /failureCode\?:\s*string/);
});

test("Not-sent label is replaced by the localized teacher refusal when failureCode is present", () => {
  const list = source("features/chat/messages/ChatMessageList.tsx");
  // The Not-sent section must call teacherTurnErrorMessage with msg.failureCode.
  assert.match(list, /msg\.failureCode/);
  assert.match(list, /teacherTurnErrorMessage.*msg\.failureCode/);
});

test("orphanedFailedTurn rendering uses teacherTurnErrorMessage with failure_code", () => {
  const list = source("features/chat/messages/ChatMessageList.tsx");
  // The orphaned-failure section must call teacherTurnErrorMessage with
  // orphanedFailure.failure_code, not just show the raw English error.
  assert.match(list, /orphanedFailure\.failure_code/);
  assert.match(list, /teacherTurnErrorMessage.*orphanedFailure\.failure_code/);
});

test("teacherTurnErrorMessage returns a localized key for known codes", async () => {
  const mod = await import("../lib/teacher-path-messages");
  const result = mod.teacherTurnErrorMessage({
    error_code: "teacher_materials_unavailable",
    materials: ["Physics notes"],
  });
  assert.ok(result);
  assert.match(result.key, /Course materials are temporarily unavailable/);
  assert.ok(result.values?.materials);

  const courseResult = mod.teacherTurnErrorMessage({
    error_code: "teacher_path_unavailable",
  });
  assert.ok(courseResult);
  assert.match(courseResult.key, /This course is no longer available/);

  // Unknown codes return null — fall through to generic display.
  assert.equal(mod.teacherTurnErrorMessage({ error_code: "internal_error" }), null);
});
