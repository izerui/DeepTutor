import { apiFetch, apiUrl } from "./api";
import type { ScopeUpdate } from "./publish-api";

/** The learning market is system-level: never workspace-scoped (platform batch 1 §8). */
const BASE = "/api/catalog";

export type CatalogKind = "book" | "knowledge_base" | "mastery_path" | "practice_set" | "course";
export const CATALOG_KINDS: readonly CatalogKind[] = ["book", "knowledge_base", "mastery_path", "practice_set", "course"];

export type ListingStatus = "none" | "draft" | "pending" | "rejected" | "listed" | "unlisted" | "taken_down";
export type SortOrder = "newest" | "popular";

/** Published for the market: nobody receives it until they join (platform batch 1 §4.4). */
export const JOIN_ONLY_SCOPE: ScopeUpdate = { everyone: false, users: [], classes: [], groups: [], join_only: true };

export interface TaxonomyName {
  zh: string;
  en: string;
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
export interface Taxonomy {
  stages: TaxonomyStage[];
  difficulties: TaxonomyNode[];
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
}

export interface MarketFacets {
  stages: Record<string, number>;
  subjects: Record<string, number>;
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
  kind?: string[];
  difficulty?: string[];
  tag?: string[];
  author?: string[];
  sort?: SortOrder;
  page?: number;
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

export interface CatalogItemDetail extends CatalogEntry {
  mine: boolean;
  closed: boolean;
  can_join: boolean;
  preview: CatalogPreview;
  study: StudyTarget;
}

export interface AuthorPage {
  id: string;
  name: string;
  role: string;
  learners: number;
  items: CatalogEntry[];
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
  for (const key of ["stage", "subject", "kind", "difficulty", "tag", "author"] as const) {
    const values = query[key];
    if (values && values.length) params.set(key, values.join(","));
  }
  if (query.sort && query.sort !== "newest") params.set("sort", query.sort);
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
