import { useState } from "react";
import { useTranslation } from "react-i18next";

import { changeOwnPassword } from "@/lib/profile-api";

/**
 * Change one's own password (spec 2026-10-29 D20: after a reset, set your own).
 * The new password is typed twice (upstream 9c7fd4a67) so a typo is caught
 * before it replaces the only password the account has.
 */
export function PasswordCard({ onChanged }: { onChanged?: () => void }) {
  const { t } = useTranslation();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const field =
    "h-9 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 text-sm text-[var(--foreground)]";
  return (
    <form
      className="mt-4 space-y-3 rounded-2xl border border-[var(--border)] bg-[var(--card)] p-6 shadow-sm"
      onSubmit={(event) => {
        event.preventDefault();
        if (next !== confirm) {
          setMessage({ ok: false, text: t("Passwords do not match") });
          return;
        }
        setBusy(true);
        setMessage(null);
        changeOwnPassword(current, next)
          .then(() => {
            setCurrent("");
            setNext("");
            setConfirm("");
            setMessage({ ok: true, text: t("Password changed. Other devices were signed out.") });
            onChanged?.();
          })
          .catch((err) => setMessage({ ok: false, text: err instanceof Error ? t(err.message) : String(err) }))
          .finally(() => setBusy(false));
      }}
    >
      <h2 className="text-sm font-semibold text-[var(--foreground)]">{t("Change password")}</h2>
      <input
        type="password"
        autoComplete="current-password"
        aria-label={t("Current password")}
        placeholder={t("Current password")}
        className={field}
        value={current}
        onChange={(e) => setCurrent(e.target.value)}
      />
      <input
        type="password"
        autoComplete="new-password"
        aria-label={t("New password (at least 8 characters)")}
        placeholder={t("New password (at least 8 characters)")}
        className={field}
        value={next}
        minLength={8}
        onChange={(e) => setNext(e.target.value)}
      />
      <input
        type="password"
        autoComplete="new-password"
        aria-label={t("Confirm new password")}
        placeholder={t("Confirm new password")}
        className={field}
        value={confirm}
        onChange={(e) => setConfirm(e.target.value)}
      />
      <button
        type="submit"
        disabled={busy || !current || next.length < 8 || !confirm}
        className="rounded-lg bg-[var(--primary)] px-3 py-1.5 text-sm font-medium text-[var(--primary-foreground)] disabled:opacity-50"
      >
        {t("Change password")}
      </button>
      {message && (
        <p role={message.ok ? "status" : "alert"} className={`text-sm ${message.ok ? "text-[var(--muted-foreground)]" : "text-red-600"}`}>
          {message.text}
        </p>
      )}
    </form>
  );
}
