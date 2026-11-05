import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

import { accountRoleLabelKey } from "../lib/account-role";

test("only administrators and learners are shown", () => {
  assert.equal(accountRoleLabelKey("admin"), "Administrator");
  assert.equal(accountRoleLabelKey("learner"), "Learner");

  for (const language of ["en", "zh", "de", "fr", "uk", "pl"]) {
    const labels = JSON.parse(
      readFileSync(path.resolve(process.cwd(), `locales/${language}/app.json`), "utf8"),
    ) as Record<string, string>;
    for (const role of ["Administrator", "Learner"]) {
      assert.ok(labels[role], `${language} is missing ${role}`);
    }
    assert.notEqual(labels.Administrator, labels.Learner, `${language} shows both roles alike`);
  }
});
