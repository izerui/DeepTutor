"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { listMyListings, type CatalogEntry } from "@/lib/catalog-api";

import { catalogError, itemHref, kindLabel, STATUS_TONE, statusLabel } from "./catalog-format";

const manageHref = (entry: CatalogEntry) => `${itemHref(entry.kind, entry.id)}&manage=1`;

/** "My listings" on the teacher workbench and the administrator's home (§5.2). */
export function MyListings() {
  const { t } = useTranslation();
  const [rows, setRows] = useState<CatalogEntry[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    listMyListings()
      .then((next) => alive && setRows(next))
      .catch((err) => alive && setError(catalogError(err, t)));
    return () => {
      alive = false;
    };
  }, [t]);

  return (
    <section aria-labelledby="my-listings" className="space-y-2" data-testid="my-listings">
      <div className="flex items-center justify-between">
        <h2 id="my-listings" className="text-[13px] font-semibold">{t("My listings")}</h2>
        <Link href="/market" className="text-[12.5px] text-[var(--primary)] hover:underline">{t("Learning market")}</Link>
      </div>
      {error && <p role="alert" className="text-[12px] text-red-600">{error}</p>}
      {rows === null ? (
        !error && <div aria-busy="true" className="h-16 animate-pulse rounded-xl bg-[var(--muted)]" />
      ) : rows.length === 0 ? (
        <p className="text-[12.5px] text-[var(--muted-foreground)]">{t("Nothing listed yet. Publish something, then list it in the market.")}</p>
      ) : (
        <ul className="divide-y divide-[var(--border)] rounded-xl border border-[var(--border)]">
          {rows.map((entry) => (
            <li key={`${entry.kind}:${entry.id}`}>
              <Link href={manageHref(entry)} className="flex flex-wrap items-center gap-2 p-2.5 text-[12.5px] hover:bg-[var(--muted)]/40">
                <span className="w-24 shrink-0 text-[11px] text-[var(--muted-foreground)]">{kindLabel(entry.kind, t)}</span>
                <span className="min-w-0 flex-1 truncate font-medium">{entry.title}</span>
                {entry.needs_classification && (
                  <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-[11px] font-medium text-amber-700 dark:text-amber-300">
                    {t("Needs classification")}
                  </span>
                )}
                <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${STATUS_TONE[entry.status] ?? ""}`}>
                  {statusLabel(entry.status, t)}
                </span>
                <span className="text-[11.5px] text-[var(--muted-foreground)]">{t("{{count}} learners", { count: entry.learners ?? 0 })}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
