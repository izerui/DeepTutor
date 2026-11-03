"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { listReviewQueue } from "@/lib/catalog-api";

/** How many listings wait for review; ``null`` while unknown. */
export function usePendingReviews(refresh = 0): number | null {
  const [count, setCount] = useState<number | null>(null);
  useEffect(() => {
    let alive = true;
    listReviewQueue()
      .then((rows) => alive && setCount(rows.length))
      .catch(() => alive && setCount(null));
    return () => {
      alive = false;
    };
  }, [refresh]);
  return count;
}

/** The administration pages, side by side: accounts and the market review (with the pending count). */
export function AdminNav({ refresh = 0 }: { refresh?: number }) {
  const { t } = useTranslation();
  const pathname = usePathname() ?? "";
  const pending = usePendingReviews(refresh);
  const tab = (active: boolean) =>
    `inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] transition-colors ${
      active ? "bg-[var(--primary)]/10 font-medium text-[var(--primary)]" : "text-[var(--muted-foreground)] hover:bg-[var(--muted)] hover:text-[var(--foreground)]"
    }`;
  return (
    <nav aria-label={t("Administration")} className="mb-6 flex flex-wrap gap-1.5">
      <Link href="/admin/users" className={tab(pathname.startsWith("/admin/users"))}>{t("User Management")}</Link>
      <Link href="/admin/review" className={tab(pathname.startsWith("/admin/review"))}>
        {t("Listing review")}
        {pending ? (
          <span className="rounded-full bg-[var(--primary)] px-1.5 text-[11px] font-semibold text-[var(--primary-foreground)]" data-testid="pending-count">
            {pending}
          </span>
        ) : null}
      </Link>
    </nav>
  );
}
