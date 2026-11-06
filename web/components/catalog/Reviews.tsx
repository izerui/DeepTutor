"use client";

import { Star } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { actionClass, primaryClass } from "@/components/common/form-styles";
import {
  deleteMyReview,
  deleteReview,
  listReviews,
  replyToReview,
  reportReview,
  REVIEW_REPLY_MAX,
  REVIEW_TEXT_MAX,
  saveMyReview,
  type ItemReview,
  type ItemReviews,
} from "@/lib/catalog-api";

import { catalogError, formatDay } from "./catalog-format";
import { ReportForm } from "./ReportForm";

const textareaClass =
  "min-h-16 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] p-2 text-[13px] outline-none focus:border-[var(--ring)]";

/** An average rounded to one decimal: 4.25 → "4.3". */
export const formatRating = (value: number) => (Math.round(value * 10) / 10).toFixed(1);

/** Five stars, filled to the (rounded) value. */
export function Stars({ value, size = 13 }: { value: number; size?: number }) {
  const { t } = useTranslation();
  const filled = Math.round(value);
  return (
    <span className="inline-flex items-center text-amber-500" role="img" aria-label={t("{{rating}} out of 5 stars", { rating: formatRating(value) })}>
      {[1, 2, 3, 4, 5].map((n) => (
        <Star key={n} size={size} aria-hidden fill={n <= filled ? "currentColor" : "none"} className={n <= filled ? "" : "text-[var(--muted-foreground)]"} />
      ))}
    </span>
  );
}

/** 1 to 5 stars, required. */
function StarPicker({ value, onChange }: { value: number; onChange: (stars: number) => void }) {
  const { t } = useTranslation();
  return (
    <div role="radiogroup" aria-label={t("Stars")} className="inline-flex items-center gap-0.5">
      {[1, 2, 3, 4, 5].map((n) => (
        <button key={n} type="button" role="radio" aria-checked={value === n} aria-label={t("{{count}} of 5 stars", { count: n })}
          className="rounded p-0.5 text-amber-500 hover:bg-[var(--muted)]" onClick={() => onChange(n)}>
          <Star size={18} aria-hidden fill={n <= value ? "currentColor" : "none"} className={n <= value ? "" : "text-[var(--muted-foreground)]"} />
        </button>
      ))}
    </div>
  );
}

/** Write or change one's own review. */
function ReviewForm({
  mine,
  busy,
  onSave,
  onCancel,
}: {
  mine: ItemReview | null;
  busy: boolean;
  onSave: (stars: number, text: string) => void;
  onCancel?: () => void;
}) {
  const { t } = useTranslation();
  const [stars, setStars] = useState(mine?.stars ?? 0);
  const [text, setText] = useState(mine?.text ?? "");
  return (
    <form className="space-y-2 rounded-lg bg-[var(--muted)]/40 p-3" data-testid="review-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (stars >= 1) onSave(stars, text.trim());
      }}>
      <p className="text-[12.5px] font-medium">{t("Your review")}</p>
      <StarPicker value={stars} onChange={setStars} />
      <textarea className={textareaClass} value={text} maxLength={REVIEW_TEXT_MAX} aria-label={t("What did you think? (optional)")}
        placeholder={t("What did you think? (optional)")} onChange={(e) => setText(e.target.value)} />
      <div className="flex flex-wrap items-center gap-2">
        <button type="submit" className={primaryClass} disabled={busy || stars < 1}>{t("Save review")}</button>
        {onCancel && <button type="button" className={actionClass} onClick={onCancel}>{t("Cancel")}</button>}
        <span className="ml-auto text-[11px] text-[var(--muted-foreground)]">{text.length} / {REVIEW_TEXT_MAX}</span>
      </div>
    </form>
  );
}

/** The author's answer to one review: one per review, changed or removed. */
function ReplyForm({ initial, busy, onSave, onCancel }: { initial: string; busy: boolean; onSave: (text: string) => void; onCancel: () => void }) {
  const { t } = useTranslation();
  const [text, setText] = useState(initial);
  return (
    <form className="mt-2 space-y-2" onSubmit={(e) => { e.preventDefault(); onSave(text.trim()); }}>
      <textarea className={textareaClass} value={text} maxLength={REVIEW_REPLY_MAX} aria-label={t("Your reply")}
        placeholder={t("Your reply")} onChange={(e) => setText(e.target.value)} />
      <div className="flex flex-wrap items-center gap-2">
        <button type="submit" className={primaryClass} disabled={busy || (!text.trim() && !initial)}>
          {text.trim() || !initial ? t("Save reply") : t("Remove reply")}
        </button>
        <button type="button" className={actionClass} onClick={onCancel}>{t("Cancel")}</button>
        <span className="ml-auto text-[11px] text-[var(--muted-foreground)]">{text.length} / {REVIEW_REPLY_MAX}</span>
      </div>
    </form>
  );
}

/**
 * Ratings and reviews on an item's intro page (follow-ups §1): the average and
 * how many rated it, the reviews newest first with the author's answers, one's
 * own review (only someone who joined, never its author), an answer per review
 * for the author, and deleting any review for administrators.
 */
export function ReviewsSection({ kind, itemId, onRated }: { kind: string; itemId: string; onRated?: (data: ItemReviews) => void }) {
  const { t } = useTranslation();
  const [data, setData] = useState<ItemReviews | null>(null);
  const [page, setPage] = useState(1);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [replying, setReplying] = useState<string | null>(null);
  const [reporting, setReporting] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    listReviews(kind, itemId)
      .then((next) => alive && setData(next))
      .catch((err) => alive && setError(catalogError(err, t)));
    return () => {
      alive = false;
    };
  }, [kind, itemId, t]);

  const run = useCallback(
    async (work: () => Promise<ItemReviews>) => {
      setBusy(true);
      setError("");
      try {
        const next = await work();
        setData(next);
        setPage(1);
        setEditing(false);
        setReplying(null);
        onRated?.(next);
      } catch (err) {
        setError(catalogError(err, t));
      } finally {
        setBusy(false);
      }
    },
    [onRated, t],
  );

  const more = async () => {
    setBusy(true);
    try {
      const next = await listReviews(kind, itemId, page + 1);
      setData((cur) =>
        cur ? { ...next, reviews: [...cur.reviews, ...next.reviews.filter((r) => !cur.reviews.some((c) => c.user_id === r.user_id))] } : next,
      );
      setPage(page + 1);
    } catch (err) {
      setError(catalogError(err, t));
    } finally {
      setBusy(false);
    }
  };

  if (!data) {
    return error ? <p role="alert" className="text-[12px] text-red-600">{error}</p> : null;
  }
  const mine = data.mine;
  const showForm = data.can_review && (!mine || editing);

  return (
    <section aria-labelledby="item-reviews" className="space-y-3" data-testid="item-reviews">
      <div className="flex flex-wrap items-center gap-2">
        <h2 id="item-reviews" className="text-[14px] font-semibold">{t("Reviews")}</h2>
        {data.average != null && data.count > 0 ? (
          <span className="inline-flex items-center gap-1.5 text-[12.5px] text-[var(--muted-foreground)]" data-testid="review-average">
            <Stars value={data.average} />
            <span className="font-medium text-[var(--foreground)]">{formatRating(data.average)}</span>
            <span>{t("Reviews: {{count}}", { count: data.count })}</span>
          </span>
        ) : (
          <span className="text-[12.5px] text-[var(--muted-foreground)]">{t("No reviews yet.")}</span>
        )}
      </div>
      {error && <p role="alert" className="text-[12px] text-red-600">{error}</p>}

      {showForm && (
        <ReviewForm key={mine?.updated_at ?? "new"} mine={mine} busy={busy}
          onSave={(stars, text) => void run(() => saveMyReview(kind, itemId, stars, text))}
          onCancel={mine ? () => setEditing(false) : undefined} />
      )}

      {data.reviews.length > 0 && (
        <ul className="divide-y divide-[var(--border)] rounded-xl border border-[var(--border)]">
          {data.reviews.map((review) => (
            <li key={review.user_id} className="space-y-1 p-3 text-[13px]" data-testid="review">
              <div className="flex flex-wrap items-center gap-2 text-[12px] text-[var(--muted-foreground)]">
                <span className="font-medium text-[var(--foreground)]">{review.mine ? t("You") : review.author || t("Learner")}</span>
                <Stars value={review.stars} size={12} />
                <span>{formatDay(review.updated_at || review.created_at)}</span>
                <span className="ml-auto flex flex-wrap gap-2">
                  {review.mine && data.can_review && !editing && (
                    <button type="button" className="text-[var(--primary)] hover:underline" onClick={() => setEditing(true)}>
                      {t("Edit review")}
                    </button>
                  )}
                  {review.can_reply && replying !== review.user_id && (
                    <button type="button" className="text-[var(--primary)] hover:underline" onClick={() => setReplying(review.user_id)}>
                      {review.reply ? t("Edit reply") : t("Reply")}
                    </button>
                  )}
                  {review.can_delete && (
                    <button type="button" className="text-red-600 hover:underline" disabled={busy}
                      aria-label={review.mine ? t("Delete my review") : t("Delete the review by {{name}}", { name: review.author || t("Learner") })}
                      onClick={() => {
                        if (!window.confirm(review.mine ? t("Delete your review?") : t("Delete this review?"))) return;
                        void run(() => (review.mine ? deleteMyReview(kind, itemId) : deleteReview(kind, itemId, review.user_id)));
                      }}>
                      {t("Delete")}
                    </button>
                  )}
                  {/* Audit fix B §1: anyone but its writer reports a review. */}
                  {review.can_report && !review.reported && reporting !== review.user_id && (
                    <button type="button" className="text-[var(--muted-foreground)] hover:underline"
                      aria-label={t("Report the review by {{name}}", { name: review.author || t("Learner") })}
                      onClick={() => setReporting(review.user_id)}>
                      {t("Report this")}
                    </button>
                  )}
                  {review.reported && <span data-testid="review-reported">{t("Reported")}</span>}
                </span>
              </div>
              {review.text && <p className="whitespace-pre-line">{review.text}</p>}
              {review.reply && replying !== review.user_id && (
                <div className="mt-1 rounded-lg bg-[var(--muted)]/50 p-2 text-[12.5px]" data-testid="review-reply">
                  <p className="text-[11.5px] font-medium text-[var(--muted-foreground)]">{t("Author's reply")}</p>
                  <p className="whitespace-pre-line">{review.reply}</p>
                </div>
              )}
              {reporting === review.user_id && (
                <ReportForm
                  label={t("Report this review")}
                  onSend={(reason, note) => reportReview(kind, itemId, review.user_id, reason, note)}
                  onCancel={() => setReporting(null)}
                  onDone={() => {
                    setReporting(null);
                    setData((cur) => cur && {
                      ...cur,
                      reviews: cur.reviews.map((r) => (r.user_id === review.user_id ? { ...r, reported: true } : r)),
                    });
                  }}
                />
              )}
              {replying === review.user_id && (
                <ReplyForm initial={review.reply ?? ""} busy={busy}
                  onSave={(text) => void run(() => replyToReview(kind, itemId, review.user_id, text))}
                  onCancel={() => setReplying(null)} />
              )}
            </li>
          ))}
        </ul>
      )}
      {data.reviews.length < data.count && (
        <button type="button" className={actionClass} disabled={busy} onClick={() => void more()}>{t("Show more reviews")}</button>
      )}
    </section>
  );
}
