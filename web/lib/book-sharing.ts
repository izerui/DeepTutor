import type { Book, BookReference } from "./book-types";

export function groupBooks(books: Book[]): { mine: Book[]; public: Book[] } {
  const groups: { mine: Book[]; public: Book[] } = { mine: [], public: [] };
  for (const book of books) {
    groups[book.group === "public" ? "public" : "mine"].push(book);
  }
  return groups;
}

/** An empty ref deliberately denotes an unregistered, locally scoped book. */
export function bookRequestRef(book: Pick<Book, "id" | "ref">): string {
  return book.ref || book.id;
}

/** Model IDs alone cannot distinguish imported collisions or workspaces. */
export function bookSelectionKey(workspaceId: string, requestedRef: string | null): string {
  return JSON.stringify([workspaceId, requestedRef]);
}

/** App Router may deliver an encoded dynamic segment on client navigation. */
export function bookRouteParam(value?: string): string | null {
  if (!value) return null;
  try {
    return decodeURIComponent(value).trim() || null;
  } catch {
    return value.trim() || null;
  }
}

export function bookKnowledgeRefs(book: Pick<Book, "source" | "knowledge_bases" | "kb_refs">): string[] {
  return book.source === "shared" && book.kb_refs
    ? book.kb_refs
    : book.knowledge_bases || [];
}

const REFERRER_LABELS: Record<string, string> = {
  mastery_template: "Mastery path",
  course: "Course",
};

export function describeBookReferences(references: BookReference[], t: (key: string) => string): string {
  return references.map(ref =>
    `${t(REFERRER_LABELS[ref.referrer_kind] ?? ref.referrer_kind)} ${ref.referrer_id}`,
  ).join(", ");
}
