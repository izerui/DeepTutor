import { apiFetch, apiUrl } from "@/lib/api";

/**
 * Administrator controls for Video Lesson (spec 2026-11-20 §2–§4).
 *
 * All three endpoints are admin-only on the server; learners never see the
 * runtime, the npm mirror or the browser check.
 */
export const VIDEO_LESSON_SETTINGS_ENDPOINT =
  "/api/capabilities/video-lesson/settings";
const STATUS_ENDPOINT = "/api/capabilities/video-lesson/status";
const INSTALL_ENDPOINT = "/api/capabilities/video-lesson/install";

export interface VideoLessonAdminSettings {
  learners_allowed: boolean;
  temperature: number;
  max_tokens: number;
  max_attempts: number;
  layout_check: boolean;
  npm_registry: string;
}

export interface VideoLessonRuntimeStatus {
  available: boolean;
  reason: "ok" | "node_missing" | "node_too_old" | "npm_missing" | string;
  node: string | null;
  node_version: string | null;
  npm: string | null;
  installed: boolean;
  runtime_dir: string;
}

export interface VideoLessonStatus {
  runtime: VideoLessonRuntimeStatus;
  min_node_major: number;
  browser: { available: boolean; path: string | null };
  npm_registry: string;
  max_attempts_limit: number;
}

async function unwrap<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = (body as { detail?: unknown }).detail;
    throw new Error(
      typeof detail === "string" ? detail : `HTTP ${response.status}`,
    );
  }
  return (await response.json()) as T;
}

export async function getVideoLessonSettings(): Promise<VideoLessonAdminSettings> {
  return unwrap(
    await apiFetch(apiUrl(VIDEO_LESSON_SETTINGS_ENDPOINT), { cache: "no-store" }),
  );
}

export async function getVideoLessonStatus(): Promise<VideoLessonStatus> {
  return unwrap(await apiFetch(apiUrl(STATUS_ENDPOINT), { cache: "no-store" }));
}

export async function installVideoLessonRuntime(): Promise<VideoLessonStatus> {
  return unwrap(await apiFetch(apiUrl(INSTALL_ENDPOINT), { method: "POST" }));
}
