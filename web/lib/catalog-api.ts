import { apiFetch, apiUrl } from "./api";
import { downloadAuthenticated } from "./authenticated-download";
import type {
  AttemptTally,
  PracticeSetResult,
  AuthorPathReport,
} from "./report-types";

/** The learning market is system-level: never workspace-scoped (platform batch 1 §8). */
const BASE = "/api/catalog";

export type CatalogKind = "book" | "knowledge_base" | "mastery_path" | "practice_set" | "course";
export const CATALOG_KINDS: readonly CatalogKind[] = ["book", "knowledge_base", "mastery_path", "practice_set", "course"];

export type ListingStatus = "none" | "draft" | "pending" | "rejected" | "listed" | "unlisted" | "taken_down";
export type SortOrder = "newest" | "popular" | "rating";

/** Chinese and English always; the built-in entries also in German, French, Polish and Ukrainian. */
export interface TaxonomyName {
  zh: string;
  en: string;
  de?: string;
  fr?: string;
  pl?: string;
  uk?: string;
}
export interface TaxonomyNode {
  id: string;
  names: TaxonomyName;
}
export interface TaxonomySubject extends TaxonomyNode {
  children: TaxonomyNode[];
}
export interface TaxonomyStage extends TaxonomyNode {
  subjects: TaxonomySubject[];
}
/** Follow-ups §2: primary and secondary school grades, a fixed list under their stage. */
export interface TaxonomyGrade extends TaxonomyNode {
  stage: string;
}
export interface Taxonomy {
  stages: TaxonomyStage[];
  difficulties: TaxonomyNode[];
  grades?: TaxonomyGrade[];
}

export interface CatalogEntry {
  kind: CatalogKind;
  id: string;
  owner_id: string;
  status: ListingStatus;
  title: string;
  summary: string;
  stages: string[];
  subjects: string[];
  /** Follow-ups §2: 0 to 3 grades, each under one of its stages. */
  grades?: string[];
  difficulty: string;
  tags: string[];
  approved_once: boolean;
  migrated: boolean;
  blocked?: boolean;
  reason: string;
  submitted_at: number | null;
  listed_at: number | null;
  updated_at: number | null;
  needs_classification: boolean;
  learners: number;
  /** ``""`` for the built-in local administrator. */
  author?: string;
  author_role?: string;
  joined?: null | "self" | "migrated" | "course";
  /** Follow-ups §1: average stars (``null`` when nobody rated it), how many rated it. */
  rating?: number | null;
  rating_count?: number;
  /** In the viewer's favorites. */
  favorited?: boolean;
}

export interface MarketFacets {
  stages: Record<string, number>;
  subjects: Record<string, number>;
  grades?: Record<string, number>;
  kinds: Record<string, number>;
  difficulties: Record<string, number>;
  tags: { tag: string; count: number }[];
  authors: { id: string; name: string; role: string; count: number }[];
}

export interface MarketPage {
  total: number;
  page: number;
  page_size: number;
  items: CatalogEntry[];
  facets: MarketFacets;
}

export interface MarketQuery {
  q?: string;
  stage?: string[];
  subject?: string[];
  grade?: string[];
  kind?: string[];
  difficulty?: string[];
  tag?: string[];
  author?: string[];
  sort?: SortOrder;
  page?: number;
  /** Only the viewer's favorites (the "My favorites" tab). */
  favorites?: boolean;
}

export interface BookPreview {
  chapters?: { id: string; title: string; pages?: number }[];
}
export interface KnowledgeBasePreview {
  documents?: string[];
}
export interface PathPreview {
  revision?: number;
  goal?: string;
  modules?: { name: string; points: string[] }[];
}
export interface PracticeSetPreview {
  question_count?: number;
  types?: Record<string, number>;
  total_points?: number;
  questions?: {
    question: string;
    question_type: string;
    options: Record<string, string>;
    correct_answer: string;
    explanation: string;
    points: number;
  }[];
}
export interface CoursePreview {
  items?: { kind: string; title: string }[];
}
export type CatalogPreview = BookPreview & KnowledgeBasePreview & PathPreview & PracticeSetPreview & CoursePreview;

export interface StudyTarget {
  kind: CatalogKind;
  id: string;
  path_id?: string;
  name?: string;
}

/** Why nobody studies it now (audit fix B §2): an administrator took it down, or its author withdrew it. */
export type Unavailable = "taken_down" | "archived";

export interface CatalogItemDetail extends CatalogEntry {
  mine: boolean;
  closed: boolean;
  unavailable?: Unavailable | null;
  can_join: boolean;
  /** Audit fix B §1: anyone but its author reports it. */
  can_report?: boolean;
  /** The viewer's report of it is still open. */
  reported?: boolean;
  preview: CatalogPreview;
  study: StudyTarget;
}

export interface AuthorPage {
  id: string;
  name: string;
  role: string;
  learners: number;
  /** Spec 2026-11-06 §2: items in the market, their average rating weighted by raters (``null`` when unrated). */
  item_count: number;
  rating: number | null;
  rating_count: number;
  items: CatalogEntry[];
}

/** Spec 2026-11-06 §3: the two rows above the market list. */
export interface FeaturedItems {
  popular: CatalogEntry[];
  top_rated: CatalogEntry[];
}

export interface AuthorListing extends CatalogEntry {
  /** Why it cannot be submitted yet; empty when ready. */
  not_ready: string;
}

export interface ListingFields {
  title: string;
  summary: string;
  stages: string[];
  subjects: string[];
  grades: string[];
  difficulty: string;
  tags: string[];
}

export interface ReviewEvent {
  action: string;
  reason: string;
  at: number;
  actor_id: string;
}

export interface ReviewItem extends CatalogEntry {
  preview: CatalogPreview;
  events: ReviewEvent[];
}

export class CatalogApiError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message);
    this.name = "CatalogApiError";
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const json = typeof init?.body === "string";
  const res = await apiFetch(apiUrl(`${BASE}${path}`), {
    ...init,
    headers: json ? { "Content-Type": "application/json", ...init?.headers } : init?.headers,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new CatalogApiError(typeof data?.detail === "string" ? data.detail : `Request failed (${res.status})`, res.status);
  }
  return (await res.json()) as T;
}

const send = (method: string, body?: unknown): RequestInit => ({
  method,
  ...(body === undefined ? {} : { body: JSON.stringify(body) }),
});
const item = (kind: string, id: string) => `/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`;

/** The query string the market sends (and keeps in the page address). */
export function marketQueryString(query: MarketQuery): string {
  const params = new URLSearchParams();
  if (query.q?.trim()) params.set("q", query.q.trim());
  for (const key of ["stage", "subject", "grade", "kind", "difficulty", "tag", "author"] as const) {
    const values = query[key];
    if (values && values.length) params.set(key, values.join(","));
  }
  if (query.sort && query.sort !== "newest") params.set("sort", query.sort);
  if (query.favorites) params.set("favorites", "1");
  if (query.page && query.page > 1) params.set("page", String(query.page));
  return params.toString();
}

export const getTaxonomy = () => call<Taxonomy>("/taxonomy");
export const searchMarket = (query: MarketQuery) => {
  const qs = marketQueryString(query);
  return call<MarketPage>(`/items${qs ? `?${qs}` : ""}`);
};
export const getCatalogItem = (kind: string, id: string) => call<CatalogItemDetail>(`/items${item(kind, id)}`);
export const joinItem = (kind: string, id: string) =>
  call<{ joined: unknown }>(`/items${item(kind, id)}/join`, send("POST"));
export const leaveItem = (kind: string, id: string) =>
  call<{ joined: unknown }>(`/items${item(kind, id)}/join`, send("DELETE"));
export const getAuthor = (authorId: string) => call<AuthorPage>(`/authors/${encodeURIComponent(authorId)}`);
export const getFeatured = () => call<FeaturedItems>("/featured");

export const listMyListings = () => call<{ listings: CatalogEntry[] }>("/listings/mine").then((r) => r.listings);
export const getListing = (kind: string, id: string) => call<AuthorListing>(`/listings${item(kind, id)}`);
export const saveListing = (kind: string, id: string, fields: ListingFields) =>
  call<AuthorListing>(`/listings${item(kind, id)}`, send("PUT", fields));
export type ListingAction = "submit" | "withdraw-request" | "unlist" | "relist";
export const listingAction = (kind: string, id: string, action: ListingAction) =>
  call<AuthorListing>(`/listings${item(kind, id)}/${action}`, send("POST"));

export const listReviewQueue = () => call<{ listings: CatalogEntry[] }>("/review").then((r) => r.listings);
export const listReviewListed = (q = "") =>
  call<{ listings: CatalogEntry[] }>(`/review/listed${q.trim() ? `?q=${encodeURIComponent(q.trim())}` : ""}`).then(
    (r) => r.listings,
  );
export const getReviewItem = (kind: string, id: string) => call<ReviewItem>(`/review${item(kind, id)}`);
export const approveListing = (kind: string, id: string) => call<CatalogEntry>(`/review${item(kind, id)}/approve`, send("POST"));
export const rejectListing = (kind: string, id: string, reason: string) =>
  call<CatalogEntry>(`/review${item(kind, id)}/reject`, send("POST", { reason }));
export const takeDownListing = (kind: string, id: string, reason: string) =>
  call<CatalogEntry>(`/review${item(kind, id)}/take-down`, send("POST", { reason }));

/** Platform batch 2 §8.3: an administrator changes another author's classification and tags. */
export interface ClassificationFields {
  stages: string[];
  subjects: string[];
  grades: string[];
  difficulty: string;
  tags: string[];
}
export const classifyListing = (kind: string, id: string, fields: ClassificationFields) =>
  call<CatalogEntry>(`/review${item(kind, id)}/classification`, send("PUT", fields));

// ── Administrators: classification and tags (platform batch 2 §8) ──

export interface AdminTaxonomyNode extends TaxonomyNode {
  hidden: boolean;
  /** Added by an administrator; built-in entries can only be hidden. */
  custom: boolean;
  /** How many listings use it. */
  in_use: number;
}
export interface AdminTaxonomySubject extends AdminTaxonomyNode {
  children: AdminTaxonomyNode[];
}
export interface AdminTaxonomyStage extends AdminTaxonomyNode {
  subjects: AdminTaxonomySubject[];
}
export interface AdminTaxonomy {
  stages: AdminTaxonomyStage[];
  difficulties: TaxonomyNode[];
}
/** German, French, Polish and Ukrainian names; given, they replace the stored ones (empty drops one). */
export type OtherNames = Partial<Record<"de" | "fr" | "pl" | "uk", string>>;
export interface TaxonomyNodeUpdate {
  name_zh?: string;
  name_en?: string;
  hidden?: boolean;
  names?: OtherNames;
}

const node = (nodeId: string) => `/admin/taxonomy/nodes/${encodeURIComponent(nodeId)}`;

export const getAdminTaxonomy = () => call<AdminTaxonomy>("/admin/taxonomy");
export const updateTaxonomyNode = (nodeId: string, fields: TaxonomyNodeUpdate) =>
  call<AdminTaxonomy>(node(nodeId), send("PUT", fields));
/** ``parent`` ``""`` adds a stage; a stage id adds a subject; a first-level subject id a sub-subject. */
export const addTaxonomyNode = (parent: string, nameZh: string, nameEn: string, names?: OtherNames) =>
  call<AdminTaxonomy>("/admin/taxonomy/nodes", send("POST", { parent, name_zh: nameZh, name_en: nameEn, ...(names ? { names } : {}) }));
export const moveTaxonomyNode = (nodeId: string, direction: "up" | "down") =>
  call<AdminTaxonomy>(`${node(nodeId)}/move`, send("POST", { direction }));
export const deleteTaxonomyNode = (nodeId: string) => call<AdminTaxonomy>(node(nodeId), send("DELETE"));

export interface TagCount {
  tag: string;
  count: number;
}
export interface TagMergeEntry {
  from: string;
  to: string;
  at: number;
}
export interface AdminTags {
  tags: TagCount[];
  merges: TagMergeEntry[];
}
export const getAdminTags = () => call<AdminTags>("/admin/tags");
export const mergeTags = (fromTag: string, toTag: string) =>
  call<AdminTags>("/admin/tags/merge", send("POST", { from_tag: fromTag, to_tag: toTag }));
export const unmergeTag = (fromTag: string) =>
  call<AdminTags>(`/admin/tags/merge/${encodeURIComponent(fromTag)}`, send("DELETE"));

// ── Learning reports of a course or a Mastery Path (platform batch 3 §1) ──

/** Only courses and Mastery Paths have learners to report on. */
export type ReportKind = "course" | "mastery_path";
export const REPORT_KINDS: readonly ReportKind[] = ["course", "mastery_path"];

/** A course's tasks for one learner: done of total. */
export interface LearnerTasks {
  done: number;
  total: number;
}

export interface LearnerPath {
  template_id: string;
  title: string;
  revision: number;
  kp_total: number;
  kp_mastered: number;
  attempts: AttemptTally;
  last_activity: number | null;
}

/** A learner's numbers on this item only, in the chosen period (``period_days``). */
export interface LearnerOverall {
  last_activity: number | null;
  period_answers: number;
  /** Tasks of this course finished in the period (0 for a Mastery Path). */
  period_tasks_done: number;
}

export interface LearnerRow {
  id: string;
  username: string;
  overall: LearnerOverall;
  paths: LearnerPath[];
  practice_sets: PracticeSetResult[];
  /** ``null`` for a Mastery Path (no tasks). */
  tasks: LearnerTasks | null;
}

export interface LearnerReport {
  kind: ReportKind;
  id: string;
  title: string;
  period_days: number | null;
  paths: { template_id: string; title: string }[];
  practice_sets: string[];
  learners: LearnerRow[];
}

export interface ReportPoints {
  template_id: string;
  title: string;
  points: { id: string; name: string; students: number; mastered: number; not_mastered: string[] }[];
}

export interface LearnerDetail {
  id: string;
  username: string;
  paths: AuthorPathReport[];
  practice_sets: PracticeSetResult[];
  tasks: LearnerTasks | null;
}

const report = (kind: string, id: string) => `/reports${item(kind, id)}`;
const period = (days?: number | null) => (days ? `?days=${days}` : "");

export const getLearnerReport = (kind: string, id: string, days?: number | null) =>
  call<LearnerReport>(`${report(kind, id)}${period(days)}`);
export const getReportPoints = (kind: string, id: string, templateId: string) =>
  call<ReportPoints>(`${report(kind, id)}/points/${encodeURIComponent(templateId)}`);
export const reportExportUrl = (kind: string, id: string, days?: number | null) =>
  apiUrl(`${BASE}${report(kind, id)}/export${period(days)}`);
/** Follow-ups §7: downloaded with the sign-in credentials, so token-only deployments work too. */
export const downloadReport = (kind: string, id: string, days?: number | null) =>
  downloadAuthenticated(reportExportUrl(kind, id, days), "learning-report.csv");
export const getLearnerDetail = (kind: string, id: string, userId: string) =>
  call<LearnerDetail>(`${report(kind, id)}/learners/${encodeURIComponent(userId)}`);

// ── Announcements of a course or a Mastery Path (platform batch 3 §3) ──

export interface ContentAnnouncement {
  id: string;
  kind: ReportKind;
  item_id: string;
  item_title: string;
  author: string;
  text: string;
  pinned: boolean;
  created_at: number;
  updated_at: number;
  can_edit: boolean;
  can_delete: boolean;
}

/**
 * What the home's announcements hold: content announcements, or on an install
 * whose classes were not retired yet, the older class announcements.
 */
export type HomeAnnouncement = Pick<ContentAnnouncement, "id" | "author" | "text" | "pinned" | "created_at"> &
  Partial<Pick<ContentAnnouncement, "kind" | "item_id" | "item_title">> & { class_id?: string; class_name?: string };

export const ANNOUNCEMENT_MAX = 5000;

export const listItemAnnouncements = (kind: string, id: string) =>
  call<{ announcements: ContentAnnouncement[] }>(`/items${item(kind, id)}/announcements`).then((r) => r.announcements);
export const postItemAnnouncement = (kind: string, id: string, text: string, pinned: boolean) =>
  call<ContentAnnouncement>(`/items${item(kind, id)}/announcements`, send("POST", { text, pinned }));
export const updateItemAnnouncement = (announcementId: string, patch: { text?: string; pinned?: boolean }) =>
  call<ContentAnnouncement>(`/announcements/${encodeURIComponent(announcementId)}`, send("PUT", patch));
export const deleteItemAnnouncement = (announcementId: string) =>
  call<{ deleted: boolean }>(`/announcements/${encodeURIComponent(announcementId)}`, send("DELETE"));
export const listMyAnnouncements = () =>
  call<{ announcements: ContentAnnouncement[] }>("/announcements/mine").then((r) => r.announcements);

// ── Reviews and favorites (follow-ups §1) ──

export const REVIEW_TEXT_MAX = 1000;
export const REVIEW_REPLY_MAX = 1000;

export interface ItemReview {
  user_id: string;
  author: string;
  stars: number;
  text: string;
  reply: string | null;
  reply_at: number | null;
  created_at: number;
  updated_at: number;
  mine: boolean;
  /** The viewer is the item's author. */
  can_reply: boolean;
  /** One's own, or any (administrators). */
  can_delete: boolean;
  /** Audit fix B §1: anyone but its writer reports it. */
  can_report?: boolean;
  /** The viewer's report of it is still open. */
  reported?: boolean;
}

export interface ItemReviews {
  average: number | null;
  count: number;
  /** Newest first, one page. */
  reviews: ItemReview[];
  mine: ItemReview | null;
  /** Joined and not its author. */
  can_review: boolean;
}

const reviews = (kind: string, id: string) => `/items${item(kind, id)}/reviews`;

export const listReviews = (kind: string, id: string, page = 1) =>
  call<ItemReviews>(`${reviews(kind, id)}${page > 1 ? `?page=${page}` : ""}`);
export const saveMyReview = (kind: string, id: string, stars: number, text: string) =>
  call<ItemReviews>(`${reviews(kind, id)}/mine`, send("PUT", { stars, text }));
export const deleteMyReview = (kind: string, id: string) => call<ItemReviews>(`${reviews(kind, id)}/mine`, send("DELETE"));
/** Administrators: anyone's review. */
export const deleteReview = (kind: string, id: string, userId: string) =>
  call<ItemReviews>(`${reviews(kind, id)}/${encodeURIComponent(userId)}`, send("DELETE"));
/** The item's author answers a review; an empty text removes the answer. */
export const replyToReview = (kind: string, id: string, userId: string, text: string) =>
  call<ItemReviews>(`${reviews(kind, id)}/${encodeURIComponent(userId)}/reply`, send("PUT", { text }));
export const setFavorite = (kind: string, id: string, on: boolean) =>
  call<{ favorited: boolean }>(`/items${item(kind, id)}/favorite`, send(on ? "PUT" : "DELETE"));

// ── reports of content and reviews (audit fix B §1) ──────

export type FlagReason = "wrong" | "infringement" | "advertising" | "inappropriate" | "other";
export const FLAG_REASONS: readonly FlagReason[] = ["wrong", "infringement", "advertising", "inappropriate", "other"];
export const FLAG_NOTE_MAX = 500;

export interface FlagReport {
  reporter: string;
  reason: FlagReason;
  note: string;
  created_at: number;
}

/** The open reports of one item or one review, gathered for an administrator. */
export interface FlagGroup {
  kind: CatalogKind;
  id: string;
  /** Empty for the item itself; the reviewer's id for a review. */
  review_user_id: string;
  target: "item" | "review";
  title: string;
  status: ListingStatus;
  author: string;
  count: number;
  reasons: Partial<Record<FlagReason, number>>;
  reports: FlagReport[];
  first_at: number | null;
  review: { author: string; stars: number | null; text: string; exists: boolean } | null;
}

export type FlagAction = "take_down" | "delete_review" | "dismiss";

export const reportItem = (kind: string, id: string, reason: FlagReason, note: string) =>
  call<{ reported: boolean }>(`/items${item(kind, id)}/report`, send("POST", { reason, note }));
export const reportReview = (kind: string, id: string, userId: string, reason: FlagReason, note: string) =>
  call<{ reported: boolean }>(`${reviews(kind, id)}/${encodeURIComponent(userId)}/report`, send("POST", { reason, note }));
export const listFlags = () => call<{ reports: FlagGroup[] }>("/review/reports").then((r) => r.reports);
export const handleFlags = (group: Pick<FlagGroup, "kind" | "id" | "review_user_id">, action: FlagAction, reason = "") =>
  call<{ handled: FlagAction }>("/review/reports/handle", send("POST", {
    kind: group.kind, item_id: group.id, review_user_id: group.review_user_id, action, reason,
  }));
