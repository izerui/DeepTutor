"use client";

import { Pin, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { actionClass, inputClass, primaryClass } from "@/components/common/form-styles";
import {
  ANNOUNCEMENT_MAX,
  deleteItemAnnouncement,
  listItemAnnouncements,
  postItemAnnouncement,
  updateItemAnnouncement,
  type ContentAnnouncement,
  type ReportKind,
} from "@/lib/catalog-api";

import { catalogError } from "./catalog-format";

const when = (seconds: number) => new Date(seconds * 1000).toLocaleString();

/**
 * Announcements of a course or a Mastery Path (platform batch 3 §3). The author
 * posts (``canPost``); each entry offers what the server allows: the author
 * edits and deletes, an administrator only deletes. Learners read; with nothing
 * to show and nothing to post, the panel stays out of the way.
 */
export function AnnouncementsPanel({
  kind,
  itemId,
  canPost = false,
}: {
  kind: ReportKind;
  itemId: string;
  canPost?: boolean;
}) {
  const { t } = useTranslation();
  const [items, setItems] = useState<ContentAnnouncement[] | null>(null);
  const [text, setText] = useState("");
  const [pinned, setPinned] = useState(false);
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(() => {
    listItemAnnouncements(kind, itemId)
      .then((next) => {
        setItems(next);
        setError("");
      })
      .catch((err) => {
        setItems((current) => current ?? []);
        setError(catalogError(err, t));
      });
  }, [kind, itemId, t]);
  useEffect(refresh, [refresh]);

  const act = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await action();
      refresh();
      return true;
    } catch (err) {
      setError(catalogError(err, t));
      return false;
    } finally {
      setBusy(false);
    }
  };

  if (!canPost && (items === null || items.length === 0) && !error) return null;
  return (
    <section aria-label={t("Announcements")} className="space-y-2" data-testid="item-announcements">
      <h3 className="text-[13px] font-medium text-[var(--foreground)]">{t("Announcements")}</h3>
      {canPost && (
        <form
          className="space-y-2"
          onSubmit={(event) => {
            event.preventDefault();
            void act(() => postItemAnnouncement(kind, itemId, text.trim(), pinned)).then((ok) => {
              if (!ok) return;
              setText("");
              setPinned(false);
            });
          }}
        >
          <textarea aria-label={t("New announcement")} placeholder={t("New announcement")} rows={3}
            maxLength={ANNOUNCEMENT_MAX} className={`${inputClass} h-auto py-2`} value={text}
            onChange={(e) => setText(e.target.value)} />
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-1.5 text-[12px]">
              <input type="checkbox" checked={pinned} onChange={(e) => setPinned(e.target.checked)} />
              {t("Pin to top")}
            </label>
            <button type="submit" className={primaryClass} disabled={busy || !text.trim()}>{t("Post")}</button>
          </div>
          <p className="text-[11px] text-[var(--muted-foreground)]">{t("Everyone who joined it is notified.")}</p>
        </form>
      )}
      {error && <p role="alert" className="text-[12px] text-[var(--destructive)]">{error}</p>}
      {items !== null && items.length === 0 && canPost ? (
        <p className="text-[12px] text-[var(--muted-foreground)]">{t("No announcements yet.")}</p>
      ) : (
        <ul className="space-y-2">
          {(items ?? []).map((item) => (
            <li key={item.id} className="rounded-lg border border-[var(--border)] p-2.5 text-[12.5px]">
              {editing?.id === item.id ? (
                <form
                  className="space-y-2"
                  onSubmit={(event) => {
                    event.preventDefault();
                    void act(() => updateItemAnnouncement(item.id, { text: editing.text.trim() })).then(
                      (ok) => ok && setEditing(null),
                    );
                  }}
                >
                  <textarea aria-label={t("Edit announcement")} rows={3} maxLength={ANNOUNCEMENT_MAX}
                    className={`${inputClass} h-auto py-2`} value={editing.text}
                    onChange={(e) => setEditing({ id: item.id, text: e.target.value })} />
                  <div className="flex gap-2">
                    <button type="submit" className={primaryClass} disabled={busy || !editing.text.trim()}>{t("Save")}</button>
                    <button type="button" className={actionClass} onClick={() => setEditing(null)}>{t("Cancel")}</button>
                  </div>
                </form>
              ) : (
                <p className="whitespace-pre-line text-[var(--foreground)]">
                  {item.pinned && <Pin size={12} className="mr-1 inline text-[var(--primary)]" aria-label={t("Pinned")} />}
                  {item.text}
                </p>
              )}
              <p className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-[var(--muted-foreground)]">
                {item.author || t("Former teacher")} · {when(item.created_at)}
                {item.updated_at > item.created_at && ` · ${t("Edited")}`}
                {item.can_edit && editing?.id !== item.id && (
                  <>
                    <button type="button" className="hover:underline" disabled={busy}
                      onClick={() => setEditing({ id: item.id, text: item.text })}>
                      {t("Edit")}
                    </button>
                    <button type="button" className="hover:underline" disabled={busy}
                      onClick={() => void act(() => updateItemAnnouncement(item.id, { pinned: !item.pinned }))}>
                      {item.pinned ? t("Unpin") : t("Pin to top")}
                    </button>
                  </>
                )}
                {item.can_delete && (
                  <button type="button" className="inline-flex items-center gap-0.5 text-red-600 hover:underline" disabled={busy}
                    onClick={() => window.confirm(t("Delete this announcement?")) && void act(() => deleteItemAnnouncement(item.id))}>
                    <Trash2 size={11} aria-hidden />
                    {t("Delete")}
                  </button>
                )}
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
