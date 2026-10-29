"use client";

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { setPublicRegistration } from "@/lib/admin-api";
import { fetchRegistrationOpen } from "@/lib/auth";

/** Public sign-up switch (spec 2026-10-29 D19). Class invite links admit either way. */
export function RegistrationToggle() {
  const { t } = useTranslation();
  const [open, setOpen] = useState<boolean | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    void fetchRegistrationOpen().then(setOpen);
  }, []);

  if (open === null) return null;
  return (
    <label
      className="flex items-center gap-2 rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm text-[var(--foreground)]"
      title={t("When off, new students can only sign up through a class invite link.")}
    >
      <input
        type="checkbox"
        checked={open}
        onChange={async (event) => {
          const next = event.target.checked;
          setError("");
          try {
            setOpen(await setPublicRegistration(next));
          } catch (err) {
            setError(err instanceof Error ? t(err.message) : String(err));
          }
        }}
      />
      {t("Open registration")}
      {error && <span role="alert" className="text-xs text-[var(--destructive)]">{error}</span>}
    </label>
  );
}
