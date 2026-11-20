"use client";

import { Clapperboard, ExternalLink, Timer } from "lucide-react";
import { useTranslation } from "react-i18next";

import MarkdownRenderer from "@/components/common/MarkdownRenderer";
import type { Block } from "@/lib/book-types";
import { bookMediaSrc } from "@/lib/book-media";

export interface VideoLessonBlockProps {
  block: Block;
}

/**
 * A chat video lesson published with the book (spec 2026-11-20 §6).
 *
 * The page runs model-written script, so it stays in a script-only sandboxed
 * frame here too; the server serves it with the same sandbox CSP as in chat.
 */
export default function VideoLessonBlock({ block }: VideoLessonBlockProps) {
  const { t } = useTranslation();
  const payload = (block.payload || {}) as Record<string, unknown>;
  const path = String(payload.lesson_url || "");
  const title = String(block.title || payload.title || "") || t("Video Lesson");
  const summary = String(payload.summary || "");
  const duration = Number(payload.duration_seconds);

  if (!path) {
    return (
      <div className="rounded-2xl border border-dashed border-[var(--border)] bg-[var(--card)]/40 p-4 text-xs italic text-[var(--muted-foreground)]">
        {t("This video lesson is no longer available.")}
      </div>
    );
  }
  const src = bookMediaSrc(path);

  return (
    <figure className="space-y-3 rounded-2xl border border-[var(--border)] bg-[var(--card)] p-3 shadow-sm">
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2 text-[13px] font-medium text-[var(--foreground)]">
          <Clapperboard size={14} className="shrink-0" />
          <span className="truncate">{title}</span>
        </div>
        <a
          href={src}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-[11px] text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)] hover:text-[var(--foreground)]"
        >
          <ExternalLink size={12} />
          {t("Open in new tab")}
        </a>
      </div>
      <iframe
        src={src}
        title={title}
        sandbox="allow-scripts"
        allow="fullscreen"
        allowFullScreen
        referrerPolicy="no-referrer"
        loading="lazy"
        className="aspect-video w-full rounded-xl border border-[var(--border)] bg-[#fdfcf9]"
      />
      {Number.isFinite(duration) && duration > 0 ? (
        <div className="flex items-center gap-2 text-[11px] text-[var(--muted-foreground)]">
          <span className="inline-flex items-center gap-1 rounded-full border border-[var(--border)] px-2 py-0.5">
            <Timer size={12} />
            {t("{{seconds}} s", { seconds: duration })}
          </span>
        </div>
      ) : null}
      {summary ? (
        <figcaption className="text-xs leading-snug text-[var(--muted-foreground)]">
          <MarkdownRenderer content={summary} variant="default" />
        </figcaption>
      ) : null}
    </figure>
  );
}
