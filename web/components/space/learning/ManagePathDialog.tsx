"use client";

import { useRef, useState, type RefObject } from "react";
import { Archive, Trash2, X } from "lucide-react";
import Link from "next/link";
import { useTranslation } from "react-i18next";

import { useAuthStatus } from "@/hooks/useAuthStatus";
import { useModalDialog } from "@/hooks/useModalDialog";
import {
  archiveTemplate,
  deleteTemplate,
  type TeacherTemplate,
} from "@/lib/teaching-api";

import { AnnouncementsPanel } from "@/components/catalog/Announcements";
import { ListingPanel } from "@/components/catalog/ListingPanel";

import { ConfirmDialog } from "./ConfirmDialog";
import { teachingErrorMessage, type Message } from "./teaching";

type Confirming = "archive" | "delete" | null;

/**
 * One teacher path's management: its market listing, learning report,
 * announcements, and archiving or deleting it. Who studies it is whoever
 * joined it (spec 2026-11-05 §1.2): nobody is added or removed by hand.
 */
export function ManagePathDialog({
  template,
  returnFocusRef,
  onClose,
  onChanged,
}: {
  template: TeacherTemplate;
  returnFocusRef: RefObject<HTMLElement | null>;
  onClose: () => void;
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const { isAdmin } = useAuthStatus();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Message | null>(null);
  const [confirming, setConfirming] = useState<Confirming>(null);
  const confirmTriggerRef = useRef<HTMLButtonElement | null>(null);
  // While a confirmation is open, Escape belongs to it.
  const dialogRef = useModalDialog(onClose, busy || confirming !== null, returnFocusRef);
  const templateId = template.id;

  const run = async (action: () => Promise<unknown>, after?: () => void) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      onChanged();
      after?.();
    } catch (reason) {
      setError(teachingErrorMessage(reason, "manage"));
    } finally {
      setBusy(false);
    }
  };

  // The server refuses to delete anything published or ever joined.
  const deletable = template.current_revision === 0 && template.assigned_count === 0;
  // Spec 2026-10-29 D10: listing a path is its teacher's choice; an
  // administrator looking at someone else's path archives or deletes it.
  const canList = template.status === "published" && template.is_owner;

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-[var(--overlay)] p-3 backdrop-blur-[2px] sm:p-5">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="manage-path-title"
        aria-busy={busy}
        tabIndex={-1}
        className="flex max-h-[90vh] w-full max-w-lg flex-col rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-2xl outline-none sm:max-h-[85vh]"
      >
        <header className="flex items-start justify-between gap-3 border-b border-[var(--border)] p-4 sm:p-5">
          <div className="min-w-0">
            <h2 id="manage-path-title" className="text-base font-semibold">
              {t("Manage path")}
            </h2>
            <p className="mt-1 truncate text-sm text-[var(--muted-foreground)]">
              {template.title}
              {template.current_revision > 0 &&
                ` · ${t("Published v{{revision}}", { revision: template.current_revision })}`}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            aria-label={t("Close")}
            className="rounded-lg p-1.5 text-[var(--muted-foreground)] hover:bg-[var(--accent)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)] disabled:opacity-40"
          >
            <X aria-hidden className="h-4 w-4" />
          </button>
        </header>

        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto overscroll-contain p-4 text-sm sm:p-5">
          {/* Platform batch 1 §4.4: the path is listed in the market; learners join it there. */}
          {canList ? (
            <>
              <ListingPanel kind="mastery_path" itemId={templateId} onChanged={onChanged} />
              {/* Platform batch 3 §1, §3: its learners' report, and announcements to them. */}
              {!isAdmin && (
              <Link
                href={`/reports?kind=mastery_path&id=${encodeURIComponent(templateId)}`}
                className="inline-flex h-8 items-center rounded-lg border border-[var(--border)] px-3 text-xs font-medium hover:bg-[var(--accent)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
              >
                {t("Learning report")}
              </Link>
              )}
              <AnnouncementsPanel kind="mastery_path" itemId={templateId} canPost />
            </>
          ) : (
            template.status === "draft" && (
              <p className="text-xs text-[var(--muted-foreground)]">
                {t("Publish this path before listing it in the learning market.")}
              </p>
            )
          )}

          {error && (
            <p role="alert" className="text-xs text-red-600">
              {t(error.key, error.values)}
            </p>
          )}
        </div>

        <footer className="flex flex-wrap justify-between gap-2 border-t border-[var(--border)] p-4">
          {deletable ? (
            <button
              type="button"
              disabled={busy}
              onClick={(event) => {
                confirmTriggerRef.current = event.currentTarget;
                setConfirming("delete");
              }}
              className="inline-flex h-9 items-center gap-1.5 rounded-lg px-3 text-sm text-red-600 hover:bg-red-500/10 focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)] disabled:opacity-50"
            >
              <Trash2 aria-hidden className="h-4 w-4" />
              {t("Delete teacher path")}
            </button>
          ) : template.status !== "archived" ? (
            <button
              type="button"
              disabled={busy}
              onClick={(event) => {
                confirmTriggerRef.current = event.currentTarget;
                setConfirming("archive");
              }}
              className="inline-flex h-9 items-center gap-1.5 rounded-lg px-3 text-sm text-red-600 hover:bg-red-500/10 focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)] disabled:opacity-50"
            >
              <Archive aria-hidden className="h-4 w-4" />
              {t("Archive")}
            </button>
          ) : (
            <span />
          )}
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="h-9 rounded-lg px-3 text-sm text-[var(--muted-foreground)] hover:bg-[var(--accent)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)] disabled:opacity-50"
          >
            {t("Close")}
          </button>
        </footer>
      </div>
      {confirming && (
        <ConfirmDialog
          title={
            confirming === "archive"
              ? t("Archive this teacher path?")
              : t("Delete this teacher path?")
          }
          description={
            confirming === "archive"
              ? t("Students can no longer open it. Their records are kept.")
              : t("The path stays in your library; only its publishing record is removed.")
          }
          confirmLabel={confirming === "archive" ? t("Archive") : t("Delete")}
          cancelLabel={t("Cancel")}
          destructive
          busy={busy}
          returnFocusRef={confirmTriggerRef}
          onConfirm={() =>
            // Closed either way: a failure is reported in this dialog, not behind it.
            void run(
              () =>
                confirming === "archive" ? archiveTemplate(templateId) : deleteTemplate(templateId),
              onClose,
            ).then(() => setConfirming(null))
          }
          onClose={() => setConfirming(null)}
        />
      )}
    </div>
  );
}
