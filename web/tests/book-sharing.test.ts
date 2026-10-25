import test from "node:test";
import assert from "node:assert/strict";
import { bookKnowledgeRefs, bookRequestRef, bookSelectionKey, describeBookReferences, groupBooks } from "../lib/book-sharing";
import type { Book } from "../lib/book-types";

const book = (id: string, extra: Partial<Book> = {}): Book =>
  ({ id, knowledge_bases: [], ...extra }) as Book;

test("library groups preserve legacy own books and public ordering", () => {
  const groups = groupBooks([book("a", { group: "mine" }), book("b", { group: "public" }), book("c")]);
  assert.deepEqual(groups.mine.map(item => item.id), ["a", "c"]);
  assert.deepEqual(groups.public.map(item => item.id), ["b"]);
});

test("only shared readers send fixed KB references", () => {
  assert.deepEqual(bookKnowledgeRefs(book("a", { source: "own", knowledge_bases: ["physics"], kb_refs: ["kb:1"] })), ["physics"]);
  assert.deepEqual(bookKnowledgeRefs(book("a", { source: "shared", knowledge_bases: ["physics"], kb_refs: ["kb:1"] })), ["kb:1"]);
  assert.deepEqual(bookKnowledgeRefs(book("a", { source: "shared", knowledge_bases: ["physics"] })), ["physics"]);
  assert.deepEqual(bookKnowledgeRefs(book("a", { source: "shared", knowledge_bases: ["physics"], kb_refs: [] })), []);
});

test("canonical and unregistered local collisions never share selection identity", () => {
  const canonical = book("same", { ref: "book:same" });
  const collision = book("same", { ref: "" });
  assert.equal(bookRequestRef(canonical), "book:same");
  assert.equal(bookRequestRef(collision), "same");
  assert.equal(canonical.id, collision.id);
  assert.notEqual(bookSelectionKey("", bookRequestRef(canonical)), bookSelectionKey("", bookRequestRef(collision)));
  assert.notEqual(bookSelectionKey("one", "same"), bookSelectionKey("two", "same"));
});

test("reference descriptions localize known kinds and retain unknown identifiers", () => {
  assert.equal(describeBookReferences([
    { book_id: "a", referrer_kind: "mastery_template", referrer_id: "tpl_1", created_by: "t1" },
    { book_id: "a", referrer_kind: "other", referrer_id: "x", created_by: "t1" },
  ], key => key === "Mastery path" ? "精通之路" : key), "精通之路 tpl_1, other x");
});
