import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const read = (file: string) => readFileSync(path.join(process.cwd(), file), "utf8");

test("admin book permissions expose only creation", () => {
  const editor = read("features/multi-user/components/BookPermissionEditor.tsx");
  assert.match(editor, /May create personal books/);
  assert.match(editor, /Per-book access has been retired/);
  assert.doesNotMatch(editor, /fetchAdminBooks|Collaborative edit|New shared books default to/);
  assert.doesNotMatch(read("features/multi-user/api.ts"), /\/api\/multi-user\/admin\/books/);
  assert.doesNotMatch(read("features/multi-user/types.ts"), /BookPermissionLevel|AdminBook/);
});

test("the existing migration notice includes all three book kinds", () => {
  const notice = read("features/multi-user/components/MigrationResultsNotice.tsx");
  for (const kind of ["book_auto_public", "book_edit_grant_retired", "book_kb_unavailable"]) {
    assert.match(notice, new RegExp(`${kind}:`));
  }
});
