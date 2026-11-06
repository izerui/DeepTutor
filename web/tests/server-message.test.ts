import assert from "node:assert/strict";
import test from "node:test";

import { serverMessage } from "../lib/server-message";

const t = (key: string, values?: Record<string, unknown>) =>
  `[${key}]${values ? JSON.stringify(values) : ""}`;

test("a private course book is translated with its title", () => {
  assert.equal(
    serverMessage(
      "Make the book “Tina's optics” public first: learners of this course cannot open it.",
      t,
    ),
    '[Make the book “{{title}}” public first: learners of this course cannot open it.]{"title":"Tina\'s optics"}',
  );
});

test("other server messages stay plain keys", () => {
  assert.equal(serverMessage("Course not found.", t), "[Course not found.]");
});
