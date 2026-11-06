import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const read = (file: string) => readFileSync(path.resolve(process.cwd(), file), "utf8");
const adminApi = read("lib/admin-api.ts");
const usersPage = read("app/(admin)/admin/users/page.tsx");
const grantEditor = read("features/multi-user/components/GrantEditor.tsx");

test("admin user creation always makes a standard learner", () => {
  // Spec 2026-11-06: no "learning restrictions" when an administrator adds someone.
  assert.match(
    adminApi,
    /body: JSON\.stringify\(\{ username, password, role: "learner", preset \}\)/,
  );
  assert.match(adminApi, /const preset: AccountPreset = "standard";/);
  assert.doesNotMatch(usersPage, /"teacher"|"student"/);
  assert.doesNotMatch(usersPage, /Learning restrictions|createRestricted/);
});

test("grant editing has no learning policy controls", () => {
  assert.doesNotMatch(
    grantEditor,
    /learning_policy|LearningPolicy|toggleReadingMaterial|toggleReadingExtension|lockLearningPolicy/,
  );
});
