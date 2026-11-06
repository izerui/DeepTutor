"use client";

import { useState } from "react";
import { useTranslation } from "react-i18next";

import { actionClass, inputClass, primaryClass } from "@/components/common/form-styles";
import { FLAG_NOTE_MAX, FLAG_REASONS, type FlagReason } from "@/lib/catalog-api";

import { catalogError } from "./catalog-format";

type Translate = (key: string, values?: Record<string, unknown>) => string;

/** The reasons a learner gives for a report (audit fix B §1). */
export function flagReasonLabel(reason: string, t: Translate): string {
  switch (reason) {
    case "wrong":
      return t("Wrong content");
    case "infringement":
      return t("Infringement or plagiarism");
    case "advertising":
      return t("Advertising");
    case "inappropriate":
      return t("Inappropriate content");
    case "other":
      return t("Other reason");
    default:
      return reason;
  }
}

/**
 * Report an item or a review (audit fix B §1): a reason and a few words.
 * Nothing is taken down automatically; an administrator looks at it.
 */
export function ReportForm({
  label,
  onSend,
  onDone,
  onCancel,
}: {
  /** What is reported, as a heading. */
  label: string;
  onSend: (reason: FlagReason, note: string) => Promise<unknown>;
  onDone: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const [reason, setReason] = useState<FlagReason | "">("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const trimmed = note.trim();
  const ready = reason !== "" && (reason !== "other" || trimmed !== "");

  const send = async () => {
    if (!ready) return;
    setBusy(true);
    setError("");
    try {
      await onSend(reason, trimmed);
      onDone();
    } catch (err) {
      setError(catalogError(err, t));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form
      className="space-y-2 rounded-lg border border-[var(--border)] bg-[var(--muted)]/30 p-3 text-[12.5px]"
      data-testid="report-form"
      onSubmit={(e) => {
        e.preventDefault();
        void send();
      }}
    >
      <fieldset disabled={busy} className="space-y-2">
        <legend className="font-medium">{label}</legend>
        <div role="radiogroup" aria-label={t("Reason for the report")} className="flex flex-wrap gap-x-4 gap-y-1">
          {FLAG_REASONS.map((value) => (
            <label key={value} className="inline-flex items-center gap-1.5">
              <input type="radio" name="report-reason" value={value} checked={reason === value} onChange={() => setReason(value)} />
              {flagReasonLabel(value, t)}
            </label>
          ))}
        </div>
        <textarea
          className={`${inputClass} h-16 py-2`}
          value={note}
          maxLength={FLAG_NOTE_MAX}
          aria-label={t("What is wrong? (optional)")}
          placeholder={reason === "other" ? t("What is wrong?") : t("What is wrong? (optional)")}
          onChange={(e) => setNote(e.target.value)}
        />
        <p className="text-[11.5px] text-[var(--muted-foreground)]">
          {t("An administrator looks at every report. Nothing is removed automatically.")}
        </p>
        <div className="flex flex-wrap gap-2">
          <button type="submit" className={primaryClass} disabled={!ready}>{t("Send report")}</button>
          <button type="button" className={actionClass} onClick={onCancel}>{t("Cancel")}</button>
        </div>
      </fieldset>
      {error && <p role="alert" className="text-[12px] text-red-600">{error}</p>}
    </form>
  );
}
