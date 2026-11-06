"use client";

import { useTranslation } from "react-i18next";

import { useAuthStatus } from "@/hooks/useAuthStatus";

/**
 * Partners connect IM bots (WeChat, Feishu, QQ…) and run them on the server,
 * on shared resources: administrators only (audit fix B §3). The sidebar does
 * not offer them to learners; an old link lands here.
 */
export default function PartnersLayout({ children }: { children: React.ReactNode }) {
  const { t } = useTranslation();
  const { isAdmin, loading } = useAuthStatus();
  if (loading) return <div aria-busy="true" className="m-8 h-40 animate-pulse rounded-xl bg-[var(--muted)]" />;
  if (!isAdmin) {
    return (
      <p className="m-8 text-[13px] text-[var(--muted-foreground)]" data-testid="partners-admin-only">
        {t("Partners are available to administrators only.")}
      </p>
    );
  }
  return <>{children}</>;
}
