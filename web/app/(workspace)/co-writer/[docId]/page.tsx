"use client";

import { useParams } from "next/navigation";
import dynamic from "next/dynamic";
import { useAuthStatus } from "@/hooks/useAuthStatus";

import NoteWorkspace from "@/features/notes/components/NoteWorkspace";

const Companion = dynamic(() => import("@/features/notes/components/NoteCompanionWithProviders"), { ssr: false });

export default function CoWriterPage() {
  const auth = useAuthStatus();
  const params = useParams<{ docId?: string | string[] }>();
  const rawDocId = params?.docId;
  const docId = Array.isArray(rawDocId)
    ? (rawDocId[0] ?? "")
    : (rawDocId ?? "");

  const owner = auth.statusAvailable && !auth.loading
    ? (auth.enabled ? auth.userId : "local")
    : null;
  if (!owner) return null;
  return <NoteWorkspace key={`${owner}:${docId}`} docId={docId} companion={Companion} />;
}
