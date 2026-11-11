/**
 * Whether a key event is going into something the reader must not steal it from.
 *
 * A reader shortcut is bound to a modifier combination that the browser and the
 * page's own inputs also use: ⌘+ is both "zoom the page" and "grow the type",
 * and ⌘− is both "zoom out" and "shrink it". With the pointer parked over the
 * reader those bindings fire while the reader types in a note, a search box or
 * any other field, and `preventDefault` swallows the browser's own zoom — so the
 * field loses the keystroke and the page does not zoom either.
 *
 * Kept here rather than in each reader so the two cannot drift: the PDF reader
 * and the text reader bind the same three combinations and had the same gap.
 */
export function isEditableTarget(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null
  if (!element || typeof element.closest !== 'function') return false
  if (element.isContentEditable) return true
  return Boolean(element.closest('input, textarea, select, [contenteditable="true"]'))
}
