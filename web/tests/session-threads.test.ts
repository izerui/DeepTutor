import test from "node:test";
import assert from "node:assert/strict";

import type { SessionSummary } from "../lib/session-api";
import { findReusableThread, groupThreadsByMessage } from "../lib/session-threads";

function thread(
  id: string,
  preferences: SessionSummary["preferences"],
  updatedAt = 1,
): SessionSummary {
  return {
    id,
    session_id: id,
    title: id,
    created_at: 0,
    updated_at: updatedAt,
    message_count: 2,
    last_message: "",
    preferences: { session_kind: "selection_tutor", ...preferences },
  };
}

test("groups threads under their source message and skips detached or archived ones", () => {
  const grouped = groupThreadsByMessage([
    thread("a", { tutor_source_message_id: 7 }),
    thread("b", { tutor_source_message_id: "7" }),
    thread("c", { tutor_source_message_id: 9 }),
    thread("detached", { tutor_source_message_id: "", tutor_source_deleted: true }),
    thread("legacy", {}),
    thread("archived", { tutor_source_message_id: 7, archived: true }),
  ]);
  assert.deepEqual(
    [...grouped.entries()].map(([key, rows]) => [key, rows.map((row) => row.id)]),
    [
      ["7", ["a", "b"]],
      ["9", ["c"]],
    ],
  );
});

test("reuses the thread for the same message and the same passage", () => {
  const threads = [
    thread("other-text", {
      tutor_source_message_id: 7,
      tutor_scope: "selection",
      tutor_selected_text: "TLB",
    }),
    thread("older", {
      tutor_source_message_id: 7,
      tutor_scope: "selection",
      tutor_selected_text: "page table",
    }, 1),
    thread("newer", {
      tutor_source_message_id: 7,
      tutor_scope: "selection",
      tutor_selected_text: "page  table",
    }, 5),
    thread("whole", { tutor_source_message_id: 7, tutor_scope: "message" }),
    thread("other-message", {
      tutor_source_message_id: 8,
      tutor_scope: "selection",
      tutor_selected_text: "page table",
    }),
  ];
  assert.equal(findReusableThread(threads, 7, "selection", " page table ")?.id, "newer");
  assert.equal(findReusableThread(threads, 7, "message", "")?.id, "whole");
  assert.equal(findReusableThread(threads, 7, "selection", "page tables"), null);
  assert.equal(findReusableThread(threads, 9, "message", ""), null);
  assert.equal(findReusableThread(threads, null, "message", ""), null);
});

test("matches a long passage against the 2000 characters the server keeps", () => {
  const long = "x".repeat(2500);
  const threads = [
    thread("long", {
      tutor_source_message_id: 3,
      tutor_scope: "selection",
      tutor_selected_text: long.slice(0, 2000),
    }),
  ];
  assert.equal(findReusableThread(threads, 3, "selection", long)?.id, "long");
});

test("a detached thread is never reused for its old message", () => {
  const threads = [
    thread("detached", {
      tutor_source_message_id: "",
      tutor_scope: "message",
      tutor_source_deleted: true,
    }),
  ];
  assert.equal(findReusableThread(threads, 7, "message", ""), null);
});
