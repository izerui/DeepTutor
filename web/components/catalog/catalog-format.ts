import { BOOKS_HOME, bookRoute, masteryTopicRoute, MASTERY_HOME } from "@/lib/learning-routes";
import { knowledgeBaseRoute } from "@/lib/resource-routes";
import type { CatalogEntry, ListingStatus, StudyTarget } from "@/lib/catalog-api";

type Translate = (key: string, values?: Record<string, unknown>) => string;

export function kindLabel(kind: string, t: Translate): string {
  switch (kind) {
    case "book":
      return t("Book");
    case "knowledge_base":
      return t("Knowledge base");
    case "mastery_path":
      return t("Mastery Path");
    case "practice_set":
      return t("Practice set");
    case "course":
      return t("Course");
    default:
      return kind;
  }
}

export function statusLabel(status: ListingStatus | string, t: Translate): string {
  switch (status) {
    case "none":
      return t("Not listed");
    case "draft":
      return t("Not submitted");
    case "pending":
      return t("Waiting for review");
    case "rejected":
      return t("Not approved");
    case "listed":
      return t("In the market");
    case "unlisted":
      return t("Taken out of the market");
    case "taken_down":
      return t("Removed by the platform");
    default:
      return status;
  }
}

export const STATUS_TONE: Record<string, string> = {
  none: "bg-[var(--muted)] text-[var(--muted-foreground)]",
  draft: "bg-[var(--muted)] text-[var(--muted-foreground)]",
  pending: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  rejected: "bg-red-500/10 text-red-700 dark:text-red-300",
  listed: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  unlisted: "bg-[var(--muted)] text-[var(--muted-foreground)]",
  taken_down: "bg-red-500/10 text-red-700 dark:text-red-300",
};

/** Who published it: the platform (an administrator), or its author by name. */
export function authorLabel(entry: Pick<CatalogEntry, "author" | "author_role">, t: Translate): string {
  if (entry.author_role === "admin") return t("Published by the platform");
  return entry.author || t("Platform");
}

/** The intro page of an item in the market. */
export const itemHref = (kind: string, id: string) =>
  `/market/item?kind=${encodeURIComponent(kind)}&id=${encodeURIComponent(id)}`;
export const authorHref = (id: string) => `/market/author?id=${encodeURIComponent(id)}`;

/** Where "Go study" leads once joined. */
export function studyHref(target: StudyTarget): string {
  switch (target.kind) {
    case "book":
      return target.id ? bookRoute(`book:${target.id}`, null, "") : BOOKS_HOME;
    case "knowledge_base":
      return knowledgeBaseRoute(target.name);
    case "mastery_path":
      return target.path_id ? masteryTopicRoute(target.path_id, "") : MASTERY_HOME;
    case "practice_set":
      return `/practice-sets?set=${encodeURIComponent(target.id)}`;
    case "course":
      return `/tasks?course=${encodeURIComponent(target.id)}`;
    default:
      return "/market";
  }
}

export const formatDay = (seconds: number | null | undefined) =>
  seconds ? new Date(seconds * 1000).toLocaleDateString() : "";

/** The server's message (English, as an i18n key) in the reader's language. */
export function catalogError(err: unknown, t: Translate): string {
  const message = err instanceof Error ? err.message : String(err);
  return t(message);
}
