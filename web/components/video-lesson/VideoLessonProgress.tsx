"use client";

import { Check, Circle, Loader2 } from "lucide-react";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import {
  VIDEO_LESSON_STAGES,
  videoLessonProgress,
  type VideoLessonStage,
} from "@/lib/video-lesson-types";
import type { StreamEvent } from "@/features/chat/model/protocol";

const STAGE_LABELS: Record<VideoLessonStage, string> = {
  code_generation: "Writing the lesson",
  render_check: "Checking the animation",
  render_output: "Preparing the player",
};

/** The three pipeline stages of a video lesson that is still being made. */
export default function VideoLessonProgress({
  events,
}: {
  events: StreamEvent[];
}) {
  const { t } = useTranslation();
  const progress = useMemo(() => videoLessonProgress(events), [events]);

  return (
    <div
      className="mb-3 rounded-2xl border border-[var(--border)] bg-[var(--card)]/70 p-3"
      data-testid="video-lesson-progress"
      aria-live="polite"
    >
      <ol className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px]">
        {VIDEO_LESSON_STAGES.map((stage) => {
          const running = progress.current === stage;
          const done = progress.started.includes(stage) && !running;
          return (
            <li
              key={stage}
              data-state={running ? "running" : done ? "done" : "pending"}
              className={
                "inline-flex items-center gap-1.5 " +
                (running || done
                  ? "text-[var(--foreground)]"
                  : "text-[var(--muted-foreground)]")
              }
            >
              {running ? (
                <Loader2 size={12} className="animate-spin" />
              ) : done ? (
                <Check size={12} />
              ) : (
                <Circle size={12} />
              )}
              {t(STAGE_LABELS[stage])}
            </li>
          );
        })}
      </ol>
      {progress.message ? (
        <p className="mt-2 text-[11px] text-[var(--muted-foreground)]">
          {progress.message}
        </p>
      ) : null}
    </div>
  );
}
