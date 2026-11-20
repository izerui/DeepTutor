/**
 * Files a book block plays (spec 2026-11-20 §6).
 *
 * Generated media is published with the book and read through the book's own
 * access check, so every reader of a shared, listed or course book can play
 * it — not only the author whose private outputs it was generated into.
 */
import { apiUrl } from "@/lib/api";
import { currentMasteryTemplate, withMasteryTemplate } from "@/lib/mastery-template-scope";

const OUTPUTS_PREFIX = "/files/outputs/";
const BOOK_REF_PREFIX = "book:";

/** The reader route of one published file of a block. */
export function bookMediaPath(
  bookId: string,
  pageId: string,
  blockId: string,
  name: string,
): string {
  const part = encodeURIComponent;
  return `/api/books/${part(bookId)}/pages/${part(pageId)}/blocks/${part(blockId)}/media/${part(name)}`;
}

/**
 * A browser URL for a book media path. A book opened from an author path's
 * Study page carries the path id, which the server needs to authorize it.
 */
export function bookMediaSrc(path: string): string {
  if (!path) return path;
  if (path.startsWith("http://") || path.startsWith("https://")) return path;
  return withMasteryTemplate(apiUrl(path), currentMasteryTemplate());
}

/**
 * An older animation block still names the author's own outputs
 * (`/files/outputs/workspace/chat/math_animator/book-<book>-<block>/artifacts/<file>`),
 * which no other account can open. Read the same file through the book.
 */
export function bookAnimationPath(
  url: string,
  bookId: string | undefined,
  pageId: string | undefined,
  blockId: string,
): string {
  if (!url.startsWith(OUTPUTS_PREFIX) || !bookId || !pageId) return url;
  const bareBookId = bookId.startsWith(BOOK_REF_PREFIX)
    ? bookId.slice(BOOK_REF_PREFIX.length)
    : bookId;
  const parts = url.slice(OUTPUTS_PREFIX.length).split("/");
  const turn = `book-${bareBookId}-${blockId}`;
  if (
    parts.length === 6 &&
    parts[0] === "workspace" &&
    parts[1] === "chat" &&
    parts[2] === "math_animator" &&
    parts[3] === turn &&
    parts[4] === "artifacts" &&
    parts[5]
  ) {
    return bookMediaPath(bookId, pageId, blockId, parts[5]);
  }
  return url;
}
