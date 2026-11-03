"use client";

import { Search } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { actionClass, inputClass } from "@/components/classes/ClassesHub";
import { PageHeader, pageGridClass } from "@/components/layout/FeaturePage";
import { marketQueryString, searchMarket, type MarketPage, type MarketQuery, type SortOrder } from "@/lib/catalog-api";

import { catalogError } from "./catalog-format";
import { ActiveFilters, FacetSidebar } from "./FacetSidebar";
import { ItemCard } from "./ItemCard";
import { clearConditions, hasConditions, parseMarketQuery } from "./market-query";
import { useTaxonomy } from "./useTaxonomy";

/** The learning market (platform batch 1 §5.1); every condition lives in the address. */
export default function MarketBrowser() {
  const { t } = useTranslation();
  const router = useRouter();
  const pathname = usePathname() || "/market";
  const params = useSearchParams();
  const queryKey = params?.toString() ?? "";
  const query = useMemo(() => parseMarketQuery(new URLSearchParams(queryKey)), [queryKey]);
  const { taxonomy, index, nameOf } = useTaxonomy();
  // The newest answer, and the address it answers; older answers stay shown while loading.
  const [answer, setAnswer] = useState<{ key: string; result: MarketPage | null; error: string } | null>(null);
  const result = answer?.result ?? null;
  const loading = answer?.key !== queryKey;
  const error = loading ? "" : answer?.error ?? "";
  const [words, setWords] = useState(query.q ?? "");
  const [shownQ, setShownQ] = useState(query.q ?? "");
  if (shownQ !== (query.q ?? "")) {
    // The address changed (back, a chip removed): the box follows it.
    setShownQ(query.q ?? "");
    setWords(query.q ?? "");
  }

  useEffect(() => {
    let alive = true;
    searchMarket(query)
      .then((next) => alive && setAnswer({ key: queryKey, result: next, error: "" }))
      .catch((err) => alive && setAnswer((cur) => ({ key: queryKey, result: cur?.result ?? null, error: catalogError(err, t) })));
    return () => {
      alive = false;
    };
  }, [query, queryKey, t]);

  const go = (next: MarketQuery) => {
    const qs = marketQueryString(next);
    router.replace(qs ? `${pathname}?${qs}` : pathname);
  };

  const authorNames = useMemo(
    () => Object.fromEntries((result?.facets.authors ?? []).map((a) => [a.id, a.role === "admin" || !a.name ? t("Platform") : a.name])),
    [result, t],
  );
  const pageSize = result?.page_size || 24;
  const pages = result ? Math.max(1, Math.ceil(result.total / pageSize)) : 1;
  const page = query.page ?? 1;

  return (
    <div data-testid="market-browser">
      <PageHeader
        title={t("Learning market")}
        description={t("Find books, knowledge bases, Mastery Paths, practice sets and courses, and join to learn.")}
      />
      <form
        className="mb-5 flex flex-wrap items-center gap-2"
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          go({ ...query, q: words, page: 1 });
        }}
      >
        <div className="relative min-w-[14rem] flex-1">
          <Search size={14} aria-hidden className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--muted-foreground)]" />
          <input className={`${inputClass} pl-8`} value={words} maxLength={100} aria-label={t("Search the market")}
            placeholder={t("Search titles, introductions, authors and tags")} onChange={(e) => setWords(e.target.value)} />
        </div>
        <button type="submit" className={actionClass}>{t("Search")}</button>
        <label className="flex items-center gap-1.5 text-[12.5px] text-[var(--muted-foreground)]">
          {t("Sort")}
          <select className="h-8 rounded-lg border border-[var(--border)] bg-[var(--background)] px-2 text-[12.5px] text-[var(--foreground)]"
            value={query.sort ?? "newest"} onChange={(e) => go({ ...query, sort: e.target.value as SortOrder, page: 1 })}>
            <option value="newest">{t("Newest")}</option>
            <option value="popular">{t("Most learners")}</option>
          </select>
        </label>
      </form>

      <div className="flex flex-col gap-6 md:flex-row">
        <div className="md:w-56 md:shrink-0">
          <FacetSidebar query={query} facets={result?.facets ?? null} taxonomy={taxonomy} index={index} onChange={go} />
        </div>
        <div className="min-w-0 flex-1 space-y-3">
          <ActiveFilters query={query} nameOf={nameOf} authorNames={authorNames} onChange={go} />
          {error && <p role="alert" className="text-[12px] text-red-600">{error}</p>}
          {result && (
            <p className="text-[12px] text-[var(--muted-foreground)]" role="status">
              {t("{{count}} results", { count: result.total })}
            </p>
          )}
          {!result && loading ? (
            <div aria-busy="true" className="h-40 animate-pulse rounded-xl bg-[var(--muted)]" />
          ) : result && result.items.length === 0 ? (
            <div className="space-y-2 rounded-xl border border-dashed border-[var(--border)] p-8 text-center">
              <p className="text-[13px] text-[var(--muted-foreground)]">{t("Nothing matches these conditions.")}</p>
              {hasConditions(query) && (
                <button type="button" className={actionClass} onClick={() => go(clearConditions(query))}>
                  {t("Clear filters")}
                </button>
              )}
            </div>
          ) : (
            <div className={pageGridClass(3)} aria-busy={loading}>
              {(result?.items ?? []).map((entry) => (
                <ItemCard key={`${entry.kind}:${entry.id}`} entry={entry} nameOf={nameOf} />
              ))}
            </div>
          )}
          {result && pages > 1 && (
            <nav aria-label={t("Pages")} className="flex items-center justify-center gap-3 pt-2 text-[12.5px]">
              <button type="button" className={actionClass} disabled={page <= 1} onClick={() => go({ ...query, page: page - 1 })}>
                {t("Previous")}
              </button>
              <span>{t("Page {{page}} of {{pages}}", { page, pages })}</span>
              <button type="button" className={actionClass} disabled={page >= pages} onClick={() => go({ ...query, page: page + 1 })}>
                {t("Next")}
              </button>
            </nav>
          )}
        </div>
      </div>
    </div>
  );
}
