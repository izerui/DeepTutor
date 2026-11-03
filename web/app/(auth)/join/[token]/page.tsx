"use client";

import Link from "next/link";
import { useTranslation } from "react-i18next";

/**
 * Old class invite links (spec 2026-10-29 §5). Platform batch 2 §3 retired
 * classes, so the link no longer admits anyone; point at sign-in instead.
 */
export default function JoinClassPage() {
  const { t } = useTranslation();
  return (
    <div className="w-full max-w-sm space-y-5 rounded-2xl border border-[var(--border)] bg-[var(--card)] p-7 text-center">
      <h1 className="text-[18px] font-semibold text-[var(--foreground)]">{t("This invite link is no longer used.")}</h1>
      <div className="space-y-2">
        <Link
          href="/login"
          className="block rounded-lg bg-[var(--primary)] px-4 py-2.5 text-[13.5px] font-semibold text-[var(--primary-foreground)]"
        >
          {t("Sign in")}
        </Link>
        <Link
          href="/register"
          className="block rounded-lg border border-[var(--border)] px-4 py-2.5 text-[13.5px] font-medium text-[var(--foreground)]"
        >
          {t("Create an account")}
        </Link>
      </div>
    </div>
  );
}
