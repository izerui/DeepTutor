import type { MarketQuery, SortOrder } from "@/lib/catalog-api";

export type FacetKey = "stage" | "subject" | "grade" | "kind" | "difficulty" | "tag" | "author";
export const FACET_KEYS: readonly FacetKey[] = ["stage", "subject", "grade", "kind", "difficulty", "tag", "author"];
const SORTS: readonly SortOrder[] = ["newest", "popular", "rating"];

const list = (value: string | null) => (value ? value.split(",").map((v) => v.trim()).filter(Boolean) : []);

/** The market's conditions, read from the page address (§5.1: kept in the address). */
export function parseMarketQuery(params: URLSearchParams | { get: (key: string) => string | null }): MarketQuery {
  const sort = params.get("sort");
  const page = Number(params.get("page") || 1);
  return {
    q: params.get("q") ?? "",
    stage: list(params.get("stage")),
    subject: list(params.get("subject")),
    grade: list(params.get("grade")),
    kind: list(params.get("kind")),
    difficulty: list(params.get("difficulty")),
    tag: list(params.get("tag")),
    author: list(params.get("author")),
    sort: SORTS.includes(sort as SortOrder) ? (sort as SortOrder) : "newest",
    page: Number.isFinite(page) && page >= 1 ? Math.floor(page) : 1,
    favorites: ["1", "true"].includes(params.get("favorites") ?? ""),
  };
}

/** Any narrowing condition (search words count; the sort and page do not). */
export function hasConditions(query: MarketQuery): boolean {
  return Boolean(query.q?.trim()) || FACET_KEYS.some((key) => (query[key] ?? []).length > 0);
}

/** Add or remove one value of a condition; another condition starts again on page 1. */
export function toggleValue(query: MarketQuery, key: FacetKey, value: string): MarketQuery {
  const current = query[key] ?? [];
  const next = current.includes(value) ? current.filter((v) => v !== value) : [...current, value];
  return { ...query, [key]: next, page: 1 };
}

export function removeValue(query: MarketQuery, key: FacetKey, value: string): MarketQuery {
  return { ...query, [key]: (query[key] ?? []).filter((v) => v !== value), page: 1 };
}

/** Every condition cleared; the sort and the "My favorites" tab stay. */
export function clearConditions(query: MarketQuery): MarketQuery {
  return {
    q: "", stage: [], subject: [], grade: [], kind: [], difficulty: [], tag: [], author: [],
    sort: query.sort, page: 1, favorites: query.favorites,
  };
}
