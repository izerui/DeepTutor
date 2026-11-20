/**
 * The playable page of a ``video_lesson`` result (upstream 6b7897a32, adapted).
 *
 * The page is model-written script. It is only ever shown in a script-only
 * sandboxed iframe, and the server adds a sandbox CSP to the response itself.
 */
export interface VideoLessonArtifact {
  type: "html";
  url: string;
  filename: string;
  content_type?: string;
  label?: string;
}

export interface VideoLessonResult {
  response: string;
  title: string;
  page: VideoLessonArtifact;
  code: string;
  durationSeconds: number | null;
  /** Write/compile attempts (browser layout rounds are not counted). */
  attempts: number;
}

export const VIDEO_LESSON_STAGES = [
  "code_generation",
  "render_check",
  "render_output",
] as const;

export type VideoLessonStage = (typeof VIDEO_LESSON_STAGES)[number];

/** The lesson page and its metadata from a ``video_lesson`` result event, or null. */
export function extractVideoLessonResult(
  resultMetadata: Record<string, unknown> | undefined,
): VideoLessonResult | null {
  if (!resultMetadata) return null;
  const artifacts = Array.isArray(resultMetadata.artifacts)
    ? resultMetadata.artifacts
    : [];
  const page = artifacts.find(
    (item): item is VideoLessonArtifact =>
      Boolean(item) &&
      typeof item === "object" &&
      (item as Record<string, unknown>).type === "html" &&
      typeof (item as Record<string, unknown>).url === "string" &&
      Boolean(((item as Record<string, unknown>).url as string).trim()),
  );
  if (!page) return null;
  const code =
    resultMetadata.code && typeof resultMetadata.code === "object"
      ? String((resultMetadata.code as Record<string, unknown>).content ?? "")
      : "";
  const render =
    resultMetadata.render && typeof resultMetadata.render === "object"
      ? (resultMetadata.render as Record<string, unknown>)
      : {};
  const duration = Number(render.duration_seconds);
  const attempts = Array.isArray(render.attempts)
    ? render.attempts.filter(
        (entry) =>
          !(
            entry &&
            typeof entry === "object" &&
            String((entry as Record<string, unknown>).stage ?? "").startsWith(
              "layout",
            )
          ),
      ).length
    : 0;
  return {
    response: String(resultMetadata.response ?? ""),
    title: String(resultMetadata.title ?? page.label ?? ""),
    page,
    code,
    durationSeconds: Number.isFinite(duration) && duration > 0 ? duration : null,
    attempts,
  };
}

export interface VideoLessonProgressState {
  /** Stages already started, in pipeline order. */
  started: VideoLessonStage[];
  /** The stage currently running (the last one started), if any. */
  current: VideoLessonStage | null;
  /** The latest server progress line (already in the reply language). */
  message: string;
}

interface ProgressEventLike {
  type: string;
  source?: string;
  stage?: string;
  content?: string;
  metadata?: Record<string, unknown>;
}

/** Where a running video lesson is, from the turn's stream events. */
export function videoLessonProgress(
  events: readonly ProgressEventLike[],
): VideoLessonProgressState {
  const started = new Set<VideoLessonStage>();
  let current: VideoLessonStage | null = null;
  let message = "";
  for (const event of events) {
    if (event.source !== "video_lesson") continue;
    const stage = VIDEO_LESSON_STAGES.find((value) => value === event.stage);
    if (event.type === "stage_start" && stage) {
      started.add(stage);
      current = stage;
    }
    if (
      event.type === "progress" &&
      typeof event.content === "string" &&
      event.content.trim() &&
      // Model-call status rows ("LLM call") are trace detail, not progress.
      event.metadata?.trace_kind !== "call_status"
    ) {
      message = event.content.trim();
      if (stage) {
        started.add(stage);
        current = stage;
      }
    }
  }
  return {
    started: VIDEO_LESSON_STAGES.filter((value) => started.has(value)),
    current,
    message,
  };
}
