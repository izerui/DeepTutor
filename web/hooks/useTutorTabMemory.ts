"use client";

/**
 * Bring back the Little Tutor tabs that were open beside a conversation before
 * a reload (upstream 20ac8aacf, adapted to server-owned threads).
 *
 * Saved thread ids are matched against the conversation's thread list from the
 * server, so deleted, archived or foreign threads are dropped. Nothing is saved
 * for a conversation until its restore has run: the empty tab strip a reload
 * starts with must not overwrite what was saved.
 */
import { useCallback, useEffect, useRef, useSyncExternalStore, type RefObject } from "react";

import type { SessionViewerPanelHandle } from "@/components/chat/home/SessionViewerPanel";
import type { SessionSummary } from "@/lib/session-api";
import type { SelectionTutorContext } from "@/lib/selection-tutor";
import { recoveryPrincipal, RECOVERY_PRINCIPAL_CHANGED } from "@/lib/recovery-principal";
import {
  readTutorTabMemory,
  tutorTabMemoryKey,
  writeTutorTabMemory,
} from "@/lib/tutor-tab-memory";

function subscribePrincipal(listener: () => void): () => void {
  window.addEventListener(RECOVERY_PRINCIPAL_CHANGED, listener);
  return () => window.removeEventListener(RECOVERY_PRINCIPAL_CHANGED, listener);
}

export interface TutorTabMemoryOptions {
  sessionId: string | null;
  threads: SessionSummary[];
  threadsLoaded: boolean;
  language: string;
  panelRef: RefObject<SessionViewerPanelHandle | null>;
  selectionForThread: (thread: SessionSummary) => SelectionTutorContext;
}

/** Returns the callback that saves the open tutor tabs; call it whenever the
 *  panel's tabs change. */
export function useTutorTabMemory({
  sessionId,
  threads,
  threadsLoaded,
  language,
  panelRef,
  selectionForThread,
}: TutorTabMemoryOptions): () => void {
  const principal = useSyncExternalStore(subscribePrincipal, recoveryPrincipal, () => null);
  const restoredKeyRef = useRef<string | null>(null);
  // The key embeds the principal, so it changes when the account does.
  const key = principal ? tutorTabMemoryKey(sessionId) : null;

  const persist = useCallback(() => {
    if (!key || restoredKeyRef.current !== key) return;
    const snapshot = panelRef.current?.openTutorThreads();
    if (snapshot) writeTutorTabMemory(key, snapshot);
  }, [key, panelRef]);

  useEffect(() => {
    if (!key || !threadsLoaded || restoredKeyRef.current === key) return;
    restoredKeyRef.current = key;
    const saved = readTutorTabMemory(key);
    if (!saved) return;
    const byId = new Map(threads.map((thread) => [thread.session_id, thread]));
    const selections = saved.threadIds.flatMap((id) => {
      const thread = byId.get(id);
      return thread ? [selectionForThread(thread)] : [];
    });
    // Nothing to bring back: do not load the panel just to restore no tabs.
    if (selections.length === 0) return;
    panelRef.current?.restoreTutorTabs(selections, language, saved.activeThreadId);
  }, [key, language, panelRef, selectionForThread, threads, threadsLoaded]);

  // A fresh tab learns its thread id on its first turn, which also refreshes
  // the thread list: save again then.
  useEffect(() => {
    persist();
  }, [persist, threads]);

  return persist;
}
