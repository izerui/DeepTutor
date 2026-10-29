"use client";

import { useEffect, useRef, useState, type RefObject } from "react";
import { Archive, Loader2, Trash2, UserMinus, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { useModalDialog } from "@/hooks/useModalDialog";
import {
  archiveTemplate,
  assignStudents,
  deleteTemplate,
  listAssignments,
  revokeAssignment,
  type TeacherTemplate,
  type TemplateAssignment,
} from "@/lib/teaching-api";

import { PublishScopeEditor } from "@/components/publishing/PublishScopeEditor";

import { ConfirmDialog } from "./ConfirmDialog";
import { StudentPicker } from "./StudentPicker";
import { teachingErrorMessage, type Message } from "./teaching";

type Confirming = "archive" | "delete" | null;

export function TemplateAssignmentsDialog({
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
  const [rows, setRows] = useState<TemplateAssignment[] | null>(null);
  const [adding, setAdding] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Message | null>(null);
  const [confirming, setConfirming] = useState<Confirming>(null);
  const [reload, setReload] = useState(0);
  const confirmTriggerRef = useRef<HTMLButtonElement | null>(null);
  // While a confirmation is open, Escape belongs to it.
  const dialogRef = useModalDialog(onClose, busy || confirming !== null, returnFocusRef);
  const templateId = template.id;

  useEffect(() => {
    // Only the newest list for this template may be shown.
    let alive = true;
    listAssignments(templateId)
      .then((next) => {
        if (alive) setRows(next);
      })
      .catch((reason) => {
        if (alive) setError(teachingErrorMessage(reason, "manage"));
      });
    return () => {
      alive = false;
    };
  }, [templateId, reload]);

  const run = async (action: () => Promise<unknown>, after?: () => void) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      onChanged();
      if (after) after();
      else setReload((value) => value + 1);
    } catch (reason) {
      setError(teachingErrorMessage(reason, "manage"));
    } finally {
      setBusy(false);
    }
  };

  const activeIds = (rows ?? [])
    .filter((row) => row.status === "active")
    .map((row) => row.student_user_id);
  // The server refuses to delete anything published or ever assigned.
  const deletable = template.current_revision === 0 && rows !== null && rows.length === 0;
  const canAssign = template.status === "published";

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-[var(--overlay)] p-3 backdrop-blur-[2px] sm:p-5">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="template-assignments-title"
        aria-busy={busy || rows === null}
        tabIndex={-1}
        className="flex max-h-[90vh] w-full max-w-lg flex-col rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-2xl outline-none sm:max-h-[85vh]"
      >
        <header className="flex items-start justify-between gap-3 border-b border-[var(--border)] p-4 sm:p-5">
          <div className="min-w-0">
            <h2 id="template-assignments-title" className="text-base font-semibold">
              {t("Manage assignments")}
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
          {rows === null ? (
            !error && (
              <div role="status" className="flex justify-center py-6 text-[var(--muted-foreground)]">
                <Loader2 aria-hidden className="h-5 w-5 animate-spin" />
                <span className="sr-only">{t("Loading…")}</span>
              </div>
            )
          ) : rows.length === 0 ? (
            <p className="text-xs text-[var(--muted-foreground)]">{t("No students assigned yet")}</p>
          ) : (
            <ul
              aria-label={t("Students")}
              className="divide-y divide-[var(--border)] rounded-lg border border-[var(--border)]"
            >
              {rows.map((row) => (
                <li
                  key={row.student_user_id}
                  className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-xs"
                >
                  <span className="min-w-0 flex-1 truncate font-medium">
                    {row.username || row.student_user_id}
                  </span>
                  <span className="text-[var(--muted-foreground)]">
                    {row.status === "active" ? t("Assignment active") : t("Access removed")}
                  </span>
                  <span className="text-[var(--muted-foreground)]">
                    {row.applied_revision > 0
                      ? t("Using v{{revision}}", { revision: row.applied_revision })
                      : t("Not started")}
                  </span>
                  {row.status === "active" && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() =>
                        void run(() => revokeAssignment(templateId, row.student_user_id))
                      }
                      aria-label={t("Remove access for {{name}}", {
                        name: row.username || row.student_user_id,
                      })}
                      className="inline-flex h-8 items-center gap-1 rounded-md px-2 text-red-600 hover:bg-red-500/10 focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)] disabled:opacity-50"
                    >
                      <UserMinus aria-hidden className="h-3.5 w-3.5" />
                      {t("Remove access")}
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}

          {canAssign ? (
            <section>
              <h3 className="font-medium">{t("Assign students")}</h3>
              <StudentPicker
                selected={adding}
                onChange={setAdding}
                exclude={activeIds}
                disabled={busy}
              />
              <button
                type="button"
                disabled={busy || adding.length === 0}
                onClick={() =>
                  void run(async () => {
                    await assignStudents(templateId, adding);
                    setAdding([]);
                  })
                }
                className="mt-2 inline-flex h-9 items-center rounded-lg bg-[var(--primary)] px-3 text-xs font-medium text-[var(--primary-foreground)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)] focus-visible:ring-offset-2 disabled:opacity-50"
              >
                {t("Assign students")}
              </button>
            </section>
          ) : (
            template.status === "draft" && (
              <p className="text-xs text-[var(--muted-foreground)]">
                {t("Publish this path before assigning students.")}
              </p>
            )
          )}

          {canAssign && (
            <section className="border-t border-[var(--border)] pt-4">
              <PublishScopeEditor
                kind="mastery_path"
                itemId={templateId}
                onSaved={() => {
                  onChanged();
                  setReload((value) => value + 1);
                }}
              />
            </section>
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
          ) : template.status !== "archived" && rows !== null ? (
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
