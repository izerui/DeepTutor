"use client";

import { GeogebraTabProvider } from "@/context/GeogebraTabContext";
import { QuizFollowupProvider } from "@/context/QuizFollowupContext";
import NoteCompanion from "./NoteCompanion";
import type { NoteCompanionProps } from "./note-companion-props";

export default function NoteCompanionWithProviders(props: NoteCompanionProps) {
  return (
    <QuizFollowupProvider>
      <GeogebraTabProvider>
        <NoteCompanion {...props} />
      </GeogebraTabProvider>
    </QuizFollowupProvider>
  );
}
