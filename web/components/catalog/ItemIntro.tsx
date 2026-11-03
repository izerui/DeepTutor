"use client";

import { ArrowLeft, Users } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { actionClass } from "@/components/classes/ClassesHub";
import { PAGE_TITLE_CLASS } from "@/components/layout/FeaturePage";
import { CATALOG_KINDS, getCatalogItem, type CatalogItemDetail, type CatalogKind } from "@/lib/catalog-api";

import { authorHref, authorLabel, catalogError, formatDay, kindLabel, STATUS_TONE, statusLabel } from "./catalog-format";
import { CatalogPreview } from "./CatalogPreview";
import { JoinButton } from "./JoinButton";
import { ListingPanel } from "./ListingPanel";
import { useTaxonomy } from "./useTaxonomy";

/** An item's introduction (§5.1): ``/market/item?kind=…&id=…`` (``&manage=1`` opens the author's listing). */
export default function ItemIntro() {
  const { t } = useTranslation();
  const params = useSearchParams();
  const kind = params?.get("kind") ?? "";
  const id = params?.get("id") ?? "";
  const [item, setItem] = useState<CatalogItemDetail | null>(null);
  const [error, setError] = useState("");
  const [managing, setManaging] = useState(params?.get("manage") === "1");
  const { nameOf } = useTaxonomy();

  const load = useCallback(() => {
    if (!kind || !id) return;
    getCatalogItem(kind, id)
      .then((next) => {
        setItem(next);
        setError("");
      })
      .catch((err) => setError(catalogError(err, t)));
  }, [kind, id, t]);
  useEffect(load, [load]);

  const back = (
    <Link href="/market" className="mb-4 inline-flex items-center gap-1.5 text-[12.5px] text-[var(--muted-foreground)] hover:text-[var(--foreground)]">
      <ArrowLeft size={14} />
      {t("Learning market")}
    </Link>
  );
  if (!kind || !id || !CATALOG_KINDS.includes(kind as CatalogKind)) {
    return <div>{back}<p className="text-[13px] text-[var(--muted-foreground)]">{t("Not found.")}</p></div>;
  }
  if (!item) {
    return (
      <div>
        {back}
        {error ? <p role="alert" className="text-[13px] text-red-600">{error}</p> : <div aria-busy="true" className="h-40 animate-pulse rounded-xl bg-[var(--muted)]" />}
      </div>
    );
  }

  const classification = [...item.stages, ...item.subjects].map(nameOf);
  return (
    <article className="space-y-5" data-testid="item-intro">
      {back}
      <header className="space-y-2">
        <p className="flex flex-wrap items-center gap-2 text-[12px] text-[var(--muted-foreground)]">
          <span>{kindLabel(item.kind, t)}</span>
          {item.status !== "listed" && (
            <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${STATUS_TONE[item.status] ?? ""}`}>{statusLabel(item.status, t)}</span>
          )}
        </p>
        <h1 className={PAGE_TITLE_CLASS}>{item.title}</h1>
        <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[12.5px] text-[var(--muted-foreground)]">
          <Link href={authorHref(item.owner_id)} className="text-[var(--primary)] hover:underline">{authorLabel(item, t)}</Link>
          <span className="inline-flex items-center gap-1"><Users size={12} aria-hidden />{t("{{count}} learners", { count: item.learners ?? 0 })}</span>
          {item.updated_at ? <span>{t("Updated {{date}}", { date: formatDay(item.updated_at) })}</span> : null}
        </p>
      </header>

      <div className="flex flex-wrap items-center gap-2">
        <JoinButton item={item} onChanged={load} />
        {item.mine && (
          <button type="button" className={actionClass} aria-expanded={managing} onClick={() => setManaging((v) => !v)}>
            {t("Manage listing")}
          </button>
        )}
      </div>
      {item.mine && managing && <ListingPanel kind={item.kind} itemId={item.id} onChanged={load} />}

      {item.summary && <p className="whitespace-pre-line text-[13.5px] leading-relaxed">{item.summary}</p>}

      <dl className="grid gap-x-6 gap-y-2 text-[12.5px] sm:grid-cols-[max-content_1fr]">
        {classification.length > 0 && (
          <>
            <dt className="text-[var(--muted-foreground)]">{t("Classification")}</dt>
            <dd>{classification.join(" · ")}</dd>
          </>
        )}
        {item.difficulty && (
          <>
            <dt className="text-[var(--muted-foreground)]">{t("Difficulty")}</dt>
            <dd>{nameOf(item.difficulty)}</dd>
          </>
        )}
        {item.tags.length > 0 && (
          <>
            <dt className="text-[var(--muted-foreground)]">{t("Tags")}</dt>
            <dd className="flex flex-wrap gap-1">
              {item.tags.map((tag) => (
                <Link key={tag} href={`/market?tag=${encodeURIComponent(tag)}`} className="rounded-full bg-[var(--muted)] px-2 py-0.5 text-[11.5px] hover:underline">
                  #{tag}
                </Link>
              ))}
            </dd>
          </>
        )}
      </dl>

      <section aria-label={t("Preview")}>
        <CatalogPreview kind={item.kind} itemId={item.id} preview={item.preview ?? {}} />
      </section>
    </article>
  );
}
