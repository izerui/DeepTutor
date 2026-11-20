"use client";

import { useTranslation } from "react-i18next";

import McpStoreSection from "@/components/space/McpStoreSection";
import { useAuthStatus } from "@/hooks/useAuthStatus";

/** Personal MCP is admin-only (spec 2026-11-06): learners see a short notice. */
export default function SpaceMcpPage() {
  const { t } = useTranslation();
  const { isAdmin, loading } = useAuthStatus();
  if (loading) return null;
  if (!isAdmin) {
    return (
      <p className="text-sm text-[var(--muted-foreground)]">
        {t("MCP services are managed by your administrator.")}
      </p>
    );
  }
  return <McpStoreSection />;
}
