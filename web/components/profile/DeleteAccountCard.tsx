"use client";

import { useState } from "react";
import { Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { logout } from "@/lib/auth";
import { deleteOwnAccount } from "@/lib/profile-api";
import { browserPath } from "@/lib/workspace-scope";

/** Delete one's own account (polish batch §3), confirmed with the password. */
export function DeleteAccountCard() {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <section
      aria-labelledby="delete-account-heading"
      className="mt-4 space-y-3 rounded-2xl border border-red-500/30 bg-[var(--card)] p-6 shadow-sm"
      data-testid="delete-account"
    >
      <h2 id="delete-account-heading" className="text-sm font-semibold text-[var(--foreground)]">
        {t("Delete account")}
      </h2>
      <p className="text-sm text-[var(--muted-foreground)]">
        {t("Your account and your learning records are deleted. Content you made goes to an administrator, and what you listed leaves the learning market. This cannot be undone.")}
      </p>
      {open ? (
        <form
          className="space-y-3"
          onSubmit={(event) => {
            event.preventDefault();
            setBusy(true);
            setError("");
            deleteOwnAccount(password)
              .then(async () => {
                await logout();
                window.location.replace(browserPath("/login"));
              })
              .catch((err) => {
                setError(err instanceof Error ? t(err.message) : String(err));
                setBusy(false);
              });
          }}
        >
          <input
            type="password"
            autoComplete="current-password"
            aria-label={t("Enter your password to confirm")}
            placeholder={t("Enter your password to confirm")}
            className="h-9 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 text-sm text-[var(--foreground)]"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <div className="flex flex-wrap gap-2">
            <button
              type="submit"
              disabled={busy || !password}
              className="flex items-center gap-1.5 rounded-lg bg-red-600 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
            >
              <Trash2 size={14} />
              {t("Delete my account")}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setOpen(false);
                setPassword("");
                setError("");
              }}
              className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm"
            >
              {t("Cancel")}
            </button>
          </div>
          {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
        </form>
      ) : (
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="flex items-center gap-1.5 rounded-lg border border-red-500/40 px-3 py-1.5 text-sm text-red-600 hover:bg-red-500/10 dark:text-red-400"
        >
          <Trash2 size={14} />
          {t("Delete account")}
        </button>
      )}
    </section>
  );
}
