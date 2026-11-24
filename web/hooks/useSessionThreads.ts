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
  failed: boolean;
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
    failed: boolean;
  }>({ sessionId: "", rows: NO_THREADS, loaded: false, failed: false });
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((n) => n + 1), []);

  useEffect(() => subscribeSessionChanges(refresh), [refresh]);
  const failed = state.sessionId === sessionId && state.failed;
  useEffect(() => {
    if (!failed) return;
    window.addEventListener("online", refresh);
    return () => window.removeEventListener("online", refresh);
  }, [failed, refresh]);

  useEffect(() => {
    if (!sessionId) return;
    const controller = new AbortController();
    listSessionThreads(sessionId, undefined, controller.signal)
      .then((rows) => {
        if (!controller.signal.aborted) setState({ sessionId, rows, loaded: true, failed: false });
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          const unavailable = [401, 403, 404].includes(error?.status);
          setState((previous) => ({
            sessionId,
            rows: previous.sessionId === sessionId && !unavailable ? previous.rows : NO_THREADS,
            loaded: false,
            failed: true,
          }));
        }
      });
    return () => controller.abort();
  }, [sessionId, revision]);

  const current = state.sessionId === sessionId;
  const rows = current ? state.rows : NO_THREADS;
  const byMessageId = useMemo(() => groupThreadsByMessage(rows), [rows]);

  return { threads: rows, byMessageId, loaded: current && state.loaded, failed, refresh };
}
