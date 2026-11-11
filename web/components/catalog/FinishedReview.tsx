"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { listFinishedUnreviewed } from "@/lib/catalog-api";

import { itemHref } from "./catalog-format";

const keyOf = (kind: string, id: string) => `${kind}:${id}`;

/**
 * Spec 2026-11-11 §2: what one finished and has not reviewed yet (of what is
 * in the market); nothing until loaded or when it fails. A new ``reload`` reads it again.
 */
export function useFinishedUnreviewed(reload = 0): (kind: string, id: string) => boolean {
  const [keys, setKeys] = useState<Set<string>>(() => new Set());
  useEffect(() => {
    let alive = true;
    listFinishedUnreviewed()
      .then((items) => alive && setKeys(new Set(items.map((item) => keyOf(item.kind, item.id)))))
      .catch(() => alive && setKeys(new Set()));
    return () => {
      alive = false;
    };
  }, [reload]);
  return (kind, id) => keys.has(keyOf(kind, id));
}

/** "Finished · Review it": to the item's reviews on its intro page. */
export function ReviewItLink({ kind, id }: { kind: string; id: string }) {
  const { t } = useTranslation();
  return (
    <Link href={`${itemHref(kind, id)}#reviews`} data-testid="review-it"
      className="inline-flex items-center rounded-full bg-emerald-500/15 px-2 py-0.5 text-[11px] font-medium text-emerald-700 hover:underline dark:text-emerald-300">
      {t("Finished · Review it")}
    </Link>
  );
}
