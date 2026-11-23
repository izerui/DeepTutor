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
  /** True once this conversation's threads were read from the server. A
   *  failed read leaves it false: "none" would be indistinguishable from
   *  "unknown". */
  loaded: boolean;
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
    loaded: boolean;
  }>({ sessionId: "", rows: NO_THREADS, loaded: false });
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((n) => n + 1), []);

  useEffect(() => subscribeSessionChanges(refresh), [refresh]);

  useEffect(() => {
    if (!sessionId) return;
    const controller = new AbortController();
    listSessionThreads(sessionId, undefined, controller.signal)
      .then((rows) => {
        if (!controller.signal.aborted) setState({ sessionId, rows, loaded: true });
      })
      .catch(() => {
        // A conversation whose threads cannot be read simply has none to show.
        if (!controller.signal.aborted) {
          setState({ sessionId, rows: NO_THREADS, loaded: false });
        }
      });
    return () => controller.abort();
  }, [sessionId, revision]);

  const current = state.sessionId === sessionId;
  const rows = current ? state.rows : NO_THREADS;
  const byMessageId = useMemo(() => groupThreadsByMessage(rows), [rows]);

  return { threads: rows, byMessageId, loaded: current && state.loaded, refresh };
}
