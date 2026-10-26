/**
 * R9: which teacher path a read request belongs to. The server opens a path's
 * reference-only knowledge bases and books only for original-content GETs
 * that carry it, and only for students with an active assignment.
 */
export const MASTERY_TEMPLATE_PARAM = "mastery_template";
export const MASTERY_TEMPLATE_HEADER = "X-DeepTutor-Mastery-Template";

export function withMasteryTemplate(url: string, templateId: string): string {
  if (!templateId) return url;
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}${MASTERY_TEMPLATE_PARAM}=${encodeURIComponent(templateId)}`;
}

/** The path a book page was opened for from Study, carried in its own URL. */
export function currentMasteryTemplate(): string {
  if (typeof window === "undefined") return "";
  return new URLSearchParams(window.location.search).get(MASTERY_TEMPLATE_PARAM) ?? "";
}
