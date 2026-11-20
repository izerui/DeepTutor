/**
 * Little Tutor threads filed under the message they were opened from.
 *
 * A thread records its source in ``preferences.tutor_source_message_id``.
 * Threads without one (older threads, and threads whose source message was
 * deleted, regenerated or detached) have no message-level entry point; they
 * stay reachable from Space history under their parent conversation.
 *
 * Keys are strings so numeric SQLite ids and string ids group alike.
 */

import type { SessionSummary } from "@/lib/session-api";
import { normalizeSelectedText } from "@/lib/selection-tutor";

export type TutorScope = "selection" | "message";

const STORED_SELECTION_LIMIT = 2000;

export function threadSourceMessageId(thread: SessionSummary): string | null {
  const source = thread.preferences?.tutor_source_message_id;
  if (source == null || source === "") return null;
  return String(source);
}

export function groupThreadsByMessage(
  threads: readonly SessionSummary[],
): Map<string, SessionSummary[]> {
  const grouped = new Map<string, SessionSummary[]>();
  for (const thread of threads) {
    if (thread.preferences?.archived) continue;
    const key = threadSourceMessageId(thread);
    if (key == null) continue;
    const rows = grouped.get(key);
    if (rows) rows.push(thread);
    else grouped.set(key, [thread]);
  }
  return grouped;
}

/**
 * The thread to continue when the learner asks again about the same message
 * and the same passage (or the whole message again). Newest first, so the
 * most recently used thread wins when there are several.
 */
export function findReusableThread(
  threads: readonly SessionSummary[],
  sourceMessageId: number | string | null,
  scope: TutorScope,
  selectedText: string,
): SessionSummary | null {
  if (sourceMessageId == null) return null;
  const wantedSource = String(sourceMessageId);
  const wantedText =
    scope === "message" ? "" : normalizeSelectedText(selectedText);
  let best: SessionSummary | null = null;
  for (const thread of threads) {
    const preferences = thread.preferences;
    if (!preferences || preferences.archived) continue;
    if (threadSourceMessageId(thread) !== wantedSource) continue;
    const threadScope: TutorScope =
      preferences.tutor_scope === "message" ? "message" : "selection";
    if (threadScope !== scope) continue;
    if (scope === "selection") {
      // The server keeps the first 2000 characters of the passage.
      const stored = normalizeSelectedText(preferences.tutor_selected_text ?? "");
      if (
        !stored ||
        stored !== normalizeSelectedText(wantedText.slice(0, STORED_SELECTION_LIMIT))
      ) {
        continue;
      }
    }
    if (!best || Number(thread.updated_at || 0) > Number(best.updated_at || 0)) {
      best = thread;
    }
  }
  return best;
}
