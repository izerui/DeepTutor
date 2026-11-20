"use client";

/**
 * The Little Tutor threads opened from a conversation, grouped by the message
 * each one came from. Read once per conversation and refreshed whenever the
 * session list changes (a thread's first turn, a deletion, a detach).
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { listSessionThreads, type SessionSummary } from "@/lib/session-api";
import { subscribeSessionChanges } from "@/lib/session-events";
import { groupThreadsByMessage } from "@/lib/session-threads";

const NO_THREADS: SessionSummary[] = [];

export interface SessionThreads {
  threads: SessionSummary[];
  byMessageId: ReadonlyMap<string, SessionSummary[]>;
  refresh: () => void;
}

export function useSessionThreads(
  sessionId: string | null | undefined,
): SessionThreads {
  // The rows carry the session they belong to, so switching conversations
  // never shows one chat's threads under another's messages.
  const [state, setState] = useState<{
    sessionId: string;
    rows: SessionSummary[];
  }>({ sessionId: "", rows: NO_THREADS });
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((n) => n + 1), []);

  useEffect(() => subscribeSessionChanges(refresh), [refresh]);

  useEffect(() => {
    if (!sessionId) return;
    const controller = new AbortController();
    listSessionThreads(sessionId, undefined, controller.signal)
      .then((rows) => {
        if (!controller.signal.aborted) setState({ sessionId, rows });
      })
      .catch(() => {
        // A conversation whose threads cannot be read simply has none to show.
        if (!controller.signal.aborted) {
          setState({ sessionId, rows: NO_THREADS });
        }
      });
    return () => controller.abort();
  }, [sessionId, revision]);

  const rows = state.sessionId === sessionId ? state.rows : NO_THREADS;
  const byMessageId = useMemo(() => groupThreadsByMessage(rows), [rows]);

  return { threads: rows, byMessageId, refresh };
}
