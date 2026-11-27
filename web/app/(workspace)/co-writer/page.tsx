"use client";

import dynamic from "next/dynamic";

const NotesLibrary = dynamic(() => import("@/features/notes/components/NotesLibrary"), {
  ssr: false,
});

export default function NotesRoute() {
  return <NotesLibrary />;
}
