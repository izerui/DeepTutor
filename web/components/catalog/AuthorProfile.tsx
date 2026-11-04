"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { PageHeader, pageGridClass } from "@/components/layout/FeaturePage";
import { CATALOG_KINDS, getAuthor, type AuthorPage } from "@/lib/catalog-api";

import { catalogError, kindLabel } from "./catalog-format";
import { ItemCard } from "./ItemCard";
import { useTaxonomy } from "./useTaxonomy";

/** An author's page (§5.1): their name, how many learn from them, their items by kind. */
export default function AuthorProfile() {
  const { t } = useTranslation();
  const id = useSearchParams()?.get("id") ?? "";
  const [author, setAuthor] = useState<AuthorPage | null>(null);
  const [error, setError] = useState("");
  const { nameOf, classificationOf } = useTaxonomy();

  useEffect(() => {
    if (!id) return;
    let alive = true;
    getAuthor(id)
      .then((next) => alive && setAuthor(next))
      .catch((err) => alive && setError(catalogError(err, t)));
    return () => {
      alive = false;
    };
  }, [id, t]);

  const back = (
    <Link href="/market" className="mb-4 inline-flex items-center gap-1.5 text-[12.5px] text-[var(--muted-foreground)] hover:text-[var(--foreground)]">
      <ArrowLeft size={14} />
      {t("Learning market")}
    </Link>
  );
  if (!author) {
    return (
      <div>
        {back}
        {error || !id ? (
          <p role="alert" className="text-[13px] text-red-600">{error || t("Not found.")}</p>
        ) : (
          <div aria-busy="true" className="h-40 animate-pulse rounded-xl bg-[var(--muted)]" />
        )}
      </div>
    );
  }

  const name = author.role === "admin" ? t("Published by the platform") : author.name || t("Platform");
  return (
    <div data-testid="author-profile">
      {back}
      <PageHeader title={name} description={t("{{count}} learners in total", { count: author.learners })} />
      {author.items.length === 0 ? (
        <p className="text-[13px] text-[var(--muted-foreground)]">{t("Nothing in the market yet.")}</p>
      ) : (
        <div className="space-y-6">
          {CATALOG_KINDS.map((kind) => {
            const items = author.items.filter((entry) => entry.kind === kind);
            if (items.length === 0) return null;
            return (
              <section key={kind} className="space-y-2">
                <h2 className="text-[13.5px] font-semibold">{kindLabel(kind, t)} <span className="font-normal text-[var(--muted-foreground)]">({items.length})</span></h2>
                <div className={pageGridClass(3)}>
                  {items.map((entry) => <ItemCard key={`${entry.kind}:${entry.id}`} entry={entry} nameOf={nameOf} classificationOf={classificationOf} />)}
                </div>
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}
