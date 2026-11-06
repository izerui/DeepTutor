"use client";

import Link from "next/link";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { actionClass, primaryClass } from "@/components/common/form-styles";
import { joinItem, leaveItem, type CatalogItemDetail } from "@/lib/catalog-api";

import { catalogError, studyHref } from "./catalog-format";

/**
 * Join, go study, leave (platform batch 1 §5.1). Leaving keeps every record;
 * joining again brings it all back. ``onChanged`` reloads the item.
 */
export function JoinButton({ item, onChanged }: { item: CatalogItemDetail; onChanged: () => void }) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const run = async (work: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await work();
      onChanged();
    } catch (err) {
      setError(catalogError(err, t));
    } finally {
      setBusy(false);
    }
  };

  if (item.mine) return null;
  if (item.joined && item.closed) {
    // Audit fix B §2: instead of "Go study", why it cannot be studied; the records stay.
    return (
      <p className="text-[12.5px] text-[var(--muted-foreground)]" data-testid="item-unavailable">
        {item.unavailable === "taken_down"
          ? t("This was taken down. Your learning records are kept.")
          : item.unavailable === "archived"
            ? t("Its author withdrew this. Your learning records are kept.")
            : t("This content is no longer available.")}
      </p>
    );
  }

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-2">
        {item.joined ? (
          <>
            <Link href={studyHref(item.study)} className={primaryClass}>
              {t("Go study")}
            </Link>
            {item.joined === "course" ? (
              <span className="text-[12px] text-[var(--muted-foreground)]">{t("Joined through a course")}</span>
            ) : (
              <button
                type="button"
                className={actionClass}
                disabled={busy}
                onClick={() => {
                  if (window.confirm(t("Leave this? Your records are kept, and come back if you join again.")))
                    void run(() => leaveItem(item.kind, item.id));
                }}
              >
                {t("Leave")}
              </button>
            )}
          </>
        ) : item.can_join ? (
          <button type="button" className={primaryClass} disabled={busy} onClick={() => void run(() => joinItem(item.kind, item.id))}>
            {t("Join")}
          </button>
        ) : null}
      </div>
      {error && <p role="alert" className="text-[12px] text-red-600">{error}</p>}
    </div>
  );
}
