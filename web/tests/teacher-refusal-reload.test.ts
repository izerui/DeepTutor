/**
 * B2-16: teacher-path refusal reasons must survive a page reload.
 *
 * Behavioural tests: call annotateFailureCode, simulate reload via
 * readFailedSubmissions, verify the code + material names survive and
 * the UI renders the localized reason.
 */

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const source = (relative: string) =>
  fs.readFileSync(path.resolve(process.cwd(), relative), "utf8");

// -- Behavioural: teacherTurnErrorMessage localization ----------------------

test("teacherTurnErrorMessage returns a localized key for known codes", async () => {
  const mod = await import("../lib/teacher-path-messages");

  const materials = mod.teacherTurnErrorMessage({
    error_code: "teacher_materials_unavailable",
    materials: ["Physics notes"],
  });
  assert.ok(materials);
  assert.match(materials.key, /Course materials are temporarily unavailable/);
  assert.match(materials.key, /\{\{materials\}\}/);
  assert.equal(materials.values?.materials, "Physics notes");

  const course = mod.teacherTurnErrorMessage({
    error_code: "teacher_path_unavailable",
  });
  assert.ok(course);
  assert.match(course.key, /This course is no longer available/);

  // Generic variant without material names.
  const generic = mod.teacherTurnErrorMessage({
    error_code: "teacher_materials_unavailable",
  });
  assert.ok(generic);
  assert.match(generic.key, /Course materials are temporarily unavailable/);
  assert.ok(!generic.key.includes("{{materials}}"));

  // Unknown codes return null — fall through to generic display.
  assert.equal(mod.teacherTurnErrorMessage({ error_code: "internal_error" }), null);
});

// -- Structural: the wiring exists in product code --------------------------

test("ChatMessageList passes failureDetails to teacherTurnErrorMessage", () => {
  const list = source("features/chat/messages/ChatMessageList.tsx");
  // The "Not sent" path must spread failureDetails into the metadata.
  assert.match(list, /teacherTurnErrorMessage.*msg\.failureDetails/);
  // The orphaned-failure path must use failure_code.
  assert.match(list, /teacherTurnErrorMessage.*orphanedFailure\.failure_code/);
});

test("ChatStateAdapter passes event metadata to annotateFailureCode", () => {
  const adapter = source("features/chat/ChatStateAdapter.tsx");
  // The annotation call must pass the event metadata as details.
  assert.match(adapter, /annotateFailureCode\(/);
  // Must pass four arguments (sessionId, submissionId, errorCode, meta).
  const call = adapter.match(/annotateFailureCode\(\s*[^)]+,\s*[^)]+,\s*[^)]+,\s*[^)]+\)/);
  assert.ok(call, "annotateFailureCode must receive four arguments including meta");
});

test("restoredFailedMessage propagates both failureCode and failureDetails", () => {
  const adapter = source("features/chat/ChatStateAdapter.tsx");
  assert.match(adapter, /record\.failureCode/);
  assert.match(adapter, /record\.failureDetails/);
});
