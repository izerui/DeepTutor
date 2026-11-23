/**
 * Which Little Tutor threads were open beside a conversation, so a reload can
 * bring the tabs back (upstream 20ac8aacf, adapted).
 *
 * Only server thread ids are stored. On restore they are matched against the
 * conversation's thread list from the server, so a thread that was deleted,
 * archived or belongs to someone else never comes back. Keys carry the signed-in
 * principal: another account on the same browser never sees these tabs.
 */
import { browserStorage } from "@/shared/storage";
import { recoveryPrincipal } from "@/lib/recovery-principal";

const PREFIX = "dt:chat:tutor-tabs:";
const MAX_TABS = 24;

export interface TutorTabMemory {
  threadIds: string[];
  activeThreadId: string | null;
}

/** Storage key for this account and conversation, or null when either is unknown. */
export function tutorTabMemoryKey(sessionId: string | null | undefined): string | null {
  const principal = recoveryPrincipal();
  if (!principal || !sessionId) return null;
  return `${PREFIX}${encodeURIComponent(principal)}:${encodeURIComponent(sessionId)}`;
}

export function readTutorTabMemory(key: string): TutorTabMemory | null {
  const raw = browserStorage.readRaw("local", key);
  if (!raw) return null;
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!value || typeof value !== "object") return null;
  const { version, threadIds, activeThreadId } = value as Record<string, unknown>;
  if (version !== 1 || !Array.isArray(threadIds)) return null;
  const ids = [
    ...new Set(
      threadIds.filter((id): id is string => typeof id === "string" && id.length > 0),
    ),
  ].slice(0, MAX_TABS);
  if (ids.length === 0) return null;
  return {
    threadIds: ids,
    activeThreadId:
      typeof activeThreadId === "string" && ids.includes(activeThreadId)
        ? activeThreadId
        : null,
  };
}

export function writeTutorTabMemory(key: string, memory: TutorTabMemory): void {
  const threadIds = memory.threadIds.slice(0, MAX_TABS);
  if (threadIds.length === 0) {
    browserStorage.removeRaw("local", key);
    return;
  }
  browserStorage.writeRaw(
    "local",
    key,
    JSON.stringify({
      version: 1,
      threadIds,
      activeThreadId: threadIds.includes(memory.activeThreadId ?? "")
        ? memory.activeThreadId
        : null,
    }),
  );
}
