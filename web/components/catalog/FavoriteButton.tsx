"use client";

import { Heart } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { setFavorite } from "@/lib/catalog-api";

import { catalogError } from "./catalog-format";

/**
 * Keep an item in one's favorites, or take it out (follow-ups §1). A favorite
 * is not joining; it only lists the item under "My favorites" in the market.
 */
export function FavoriteButton({
  kind,
  itemId,
  favorited,
  compact = false,
  onChanged,
}: {
  kind: string;
  itemId: string;
  favorited: boolean;
  /** An icon only (market cards); otherwise icon and words. */
  compact?: boolean;
  onChanged?: (favorited: boolean) => void;
}) {
  const { t } = useTranslation();
  const [on, setOn] = useState(favorited);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const label = on ? t("Remove from favorites") : t("Add to favorites");

  const toggle = async () => {
    setBusy(true);
    setError("");
    try {
      const next = await setFavorite(kind, itemId, !on);
      setOn(next.favorited);
      onChanged?.(next.favorited);
    } catch (err) {
      setError(catalogError(err, t));
    } finally {
      setBusy(false);
    }
  };

  return (
    <span className="inline-flex items-center gap-1.5">
      <button
        type="button"
        aria-pressed={on}
        aria-label={label}
        disabled={busy}
        data-testid="favorite-button"
        onClick={(event) => {
          // On a market card the button sits over the card's link.
          event.preventDefault();
          event.stopPropagation();
          void toggle();
        }}
        className={
          compact
            ? `rounded-full p-1 transition-colors hover:bg-[var(--muted)] disabled:opacity-50 ${on ? "text-rose-600" : "text-[var(--muted-foreground)]"}`
            : `inline-flex h-8 items-center gap-1.5 rounded-lg border border-[var(--border)] px-3 text-[12.5px] font-medium transition-colors hover:bg-[var(--muted)] disabled:opacity-50 ${on ? "text-rose-600" : "text-[var(--foreground)]"}`
        }
      >
        <Heart size={compact ? 14 : 13} aria-hidden fill={on ? "currentColor" : "none"} />
        {!compact && (on ? t("Favorited") : t("Favorite"))}
      </button>
      {error && <span role="alert" className={compact ? "sr-only" : "text-[12px] text-red-600"}>{error}</span>}
    </span>
  );
}
