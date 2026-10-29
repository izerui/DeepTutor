"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Dialog } from "@/shared/ui/Dialog";
import { bookApi, bookWithdrawalImpact } from "@/lib/book-api";
import { bookErrorMessage } from "@/lib/book-errors";
import { bookRequestRef, bookSelectionKey } from "@/lib/book-sharing";
import {
  confirmationFields,
  describeOtherReferences,
  describeWithdrawalImpact,
  NO_OTHER_REFERENCES,
  type WithdrawalImpact,
} from "@/lib/withdrawal-confirmation";
import type { Book, BookKbChoice, BookSharing } from "@/lib/book-types";

const ISSUE_MESSAGES = {
  connector: "Personal connectors cannot be shared.",
  foreign_private: "Another account's private knowledge base; readers could not use it.",
} as const;

export default function BookShareDialog(props: {
  book: Book | null;
  isAdmin: boolean;
  onClose: () => void;
  onChanged: () => void;
}) {
  if (!props.book) return null;
  return <SharingForm {...props} book={props.book}
    key={bookSelectionKey(props.book.content_workspace_id ?? "", bookRequestRef(props.book))} />;
}

function SharingForm({ book, isAdmin, onClose, onChanged }: {
  book: Book;
  isAdmin: boolean;
  onClose: () => void;
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const [sharing, setSharing] = useState<BookSharing | null>(null);
  const [choices, setChoices] = useState<Record<string, BookKbChoice>>({});
  const [force, setForce] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Rule R1: set once the server reports published teacher paths citing the
  // book; the next submission then confirms the withdrawal.
  const [impact, setImpact] = useState<WithdrawalImpact | null>(null);
  const alive = useRef(true);
  const requestedRef = bookRequestRef(book);
  const workspaceId = book.content_workspace_id ?? "";

  useEffect(() => {
    alive.current = true;
    let cancelled = false;
    void bookApi.sharing(requestedRef, workspaceId).then(next => {
      if (cancelled) return;
      setSharing(next);
      setChoices(Object.fromEntries(next.kbs.private_kbs.map(kb => [kb.kb_id, kb.default])));
    }).catch(err => {
      if (!cancelled) setError(bookErrorMessage(err, t));
    });
    return () => { cancelled = true; alive.current = false; };
  }, [requestedRef, workspaceId, t]);

  const makingPublic = (sharing?.visibility ?? book.visibility) !== "public";
  const canMakePublic = sharing?.can_make_public ?? true;
  const issues = sharing?.kbs.issues ?? [];
  const otherReferences = sharing?.other_references ?? NO_OTHER_REFERENCES;
  // References never block making a book private (rule R1).
  const blocked = makingPublic && (!canMakePublic || (issues.length > 0 && !(isAdmin && force)));
  const confirm = async () => {
    if (busy || !sharing?.can_set_visibility || blocked) return;
    setBusy(true);
    setError(null);
    try {
      await bookApi.setVisibility(requestedRef, {
        visibility: makingPublic ? "public" : "private",
        kb_choices: makingPublic ? choices : {},
        force: makingPublic && isAdmin && force,
        ...(!makingPublic ? confirmationFields(impact) : {}),
      }, workspaceId);
      if (!alive.current) return;
      onChanged();
      onClose();
    } catch (err) {
      if (!alive.current) return;
      const needed = makingPublic ? null : bookWithdrawalImpact(err);
      if (needed) setImpact(needed);
      else setError(bookErrorMessage(err, t));
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  return <Dialog open title={makingPublic ? t("Make this book public?") : t("Make this book private?")}
    onClose={onClose} closeLabel={t("Close")} busy={busy}
    footer={<>
      <button type="button" data-autofocus disabled={busy} onClick={onClose}
        className="rounded-lg px-3 py-1.5 text-sm disabled:opacity-40">{t("Cancel")}</button>
      <button type="button" disabled={busy || !sharing?.can_set_visibility || blocked}
        onClick={() => void confirm()}
        className="rounded-lg bg-[var(--primary)] px-3 py-1.5 text-sm text-[var(--primary-foreground)] disabled:opacity-40">
        {makingPublic ? t("Make public") : impact ? t("Make it private anyway") : t("Make private")}
      </button>
    </>}>
    <div className="space-y-3 text-sm">
      <p>{makingPublic
        ? t("Every signed-in account can read a public book. Each reader keeps their own progress.")
        : isAdmin && book.group !== "mine"
          ? t("Only the book's owner can open a private book, plus anything that references it.")
          : t("Only you can open a private book, plus anything that references it.")}</p>
      {!sharing && !error && <p role="status">{t("Loading…")}</p>}
      {makingPublic && !!sharing?.kbs.private_kbs.length && <fieldset className="space-y-2">
        <legend className="mb-2 font-medium">{t("Private knowledge bases this book uses")}</legend>
        {sharing.kbs.private_kbs.map(kb => <label key={kb.kb_id} className="flex flex-wrap items-center justify-between gap-2">
          <span className="break-words">{kb.name}</span>
          <select aria-label={kb.name} disabled={busy} value={choices[kb.kb_id] ?? kb.default}
            onChange={event => setChoices(current => ({ ...current, [kb.kb_id]: event.target.value as BookKbChoice }))}
            className="max-w-full rounded border border-[var(--border)] bg-[var(--card)] p-2 text-xs">
            <option value="make_public">{t("Make public")}</option>
            <option value="reference_only">{t("Readable through this book")}</option>
          </select>
        </label>)}
      </fieldset>}
      {makingPublic && issues.length > 0 && <ul className="space-y-1 text-amber-700 dark:text-amber-300">
        {issues.map(issue => <li key={`${issue.ref}:${issue.kb_id}`}>{issue.name}: {t(ISSUE_MESSAGES[issue.code])}</li>)}
      </ul>}
      {makingPublic && !!sharing?.kbs.missing.length && <p>{t("Some knowledge bases of this book no longer exist: {{items}}", { items: sharing.kbs.missing.join(", ") })}</p>}
      {!makingPublic && otherReferences.total > 0 && !impact && <p>{describeOtherReferences(otherReferences, t)}</p>}
      {!makingPublic && impact && <p role="alert" className="whitespace-pre-line text-amber-700 dark:text-amber-300">
        {describeWithdrawalImpact(impact, t)}
      </p>}
      {isAdmin && makingPublic && issues.length > 0 && <label className="flex items-start gap-2">
        <input type="checkbox" checked={force} disabled={busy} onChange={event => setForce(event.target.checked)} />
        <span>{t("Publish anyway; the blocked knowledge bases stay unavailable to readers")}</span>
      </label>}
      {error && <p role="alert" className="break-words text-red-600">{error}</p>}
    </div>
  </Dialog>;
}
