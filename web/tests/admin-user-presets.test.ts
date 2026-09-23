import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const adminApi = readFileSync(
  path.resolve(process.cwd(), "lib/admin-api.ts"),
  "utf8",
);
const usersPage = readFileSync(
  path.resolve(process.cwd(), "app/(admin)/admin/users/page.tsx"),
  "utf8",
);
const grantEditor = readFileSync(
  path.resolve(process.cwd(), "features/multi-user/components/GrantEditor.tsx"),
  "utf8",
);
const en = JSON.parse(
  readFileSync(path.resolve(process.cwd(), "locales/en/app.json"), "utf8"),
) as Record<string, string>;
const zh = JSON.parse(
  readFileSync(path.resolve(process.cwd(), "locales/zh/app.json"), "utf8"),
) as Record<string, string>;

test("admin user creation sends the selected preset", () => {
  assert.match(
    adminApi,
    /export type AccountPreset = "standard" \| "learner" \| "custom"/,
  );
  assert.match(
    adminApi,
    /body: JSON\.stringify\(\{ username, password, preset \}\)/,
  );
  assert.match(usersPage, /\["standard", "learner", "custom"\] as const/);
  assert.match(usersPage, /aria-pressed=\{createPreset === preset\}/);
});

test("grant editing exposes the server-enforced learning policy controls", () => {
  assert.match(grantEditor, /learning_policy: conservativeLearningPolicy\(\)/);
  assert.match(grantEditor, /toggleReadingMaterial/);
  assert.match(grantEditor, /toggleReadingExtension/);
  assert.match(
    grantEditor,
    /checked=\{grant\.learning_policy\.reading\.allow_upload\}/,
  );
});

test("grant editor exposes capability and surface controls", () => {
  assert.match(grantEditor, /toggleCapability/);
  assert.match(grantEditor, /toggleSurface/);
  assert.match(grantEditor, /mastery_path/);
  assert.match(grantEditor, /immersive_watching/);
  assert.match(grantEditor, /CAP_TO_SURFACE/);
  assert.match(grantEditor, /Allowed surfaces/);
});

test("grant editor prevents unchecking the default capability", () => {
  assert.match(grantEditor, /disabled=\{saving \|\| cap\.id === grant\.learning_policy!\.default_capability\}/);
});

test("grant editor locks surfaces mapped to enabled capabilities", () => {
  assert.match(grantEditor, /lockedByCap/);
  assert.match(grantEditor, /disabled=\{saving \|\| lockedByCap\}/);
});

test("account preset copy is present in both supported locales", () => {
  const keys = [
    "Account preset",
    "Learner",
    "Preset: {{preset}}",
    "Learning policy",
    "Enable learning policy",
    "Age band",
    "Assigned reading materials",
    "Allow learner uploads",
    "Reading extensions",
  ];
  for (const key of keys) {
    assert.ok(key in en, `missing English key: ${key}`);
    assert.ok(key in zh, `missing Chinese key: ${key}`);
    assert.notEqual(en[key], "");
    assert.notEqual(zh[key], "");
  }
});
