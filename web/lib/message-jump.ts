/**
 * Scroll the chat to one of its messages and flash it, for "Back to source"
 * in a Little Tutor thread. Returns false when the message is not on screen
 * in the current conversation: deleted, on another edit branch, or in a
 * different conversation.
 */

const HIGHLIGHT_ATTRIBUTE = "data-tutor-source-highlight";
const HIGHLIGHT_MS = 2000;

export function jumpToChatMessage(
  messageId: number | string | null | undefined,
  root: ParentNode = document,
): boolean {
  if (messageId == null || messageId === "") return false;
  const id = String(messageId);
  if (!/^[\w-]+$/.test(id)) return false;
  const element = root.querySelector<HTMLElement>(
    `[data-chat-message-id="${id}"]`,
  );
  if (!element) return false;
  element.scrollIntoView?.({ block: "center", behavior: "auto" });
  element.setAttribute(HIGHLIGHT_ATTRIBUTE, "true");
  window.setTimeout(() => element.removeAttribute(HIGHLIGHT_ATTRIBUTE), HIGHLIGHT_MS);
  return true;
}
