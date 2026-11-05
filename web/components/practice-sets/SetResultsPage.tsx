"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useTranslation } from "react-i18next";

import { PAGE_TITLE_CLASS } from "@/components/layout/FeaturePage";
import { SetResults } from "./SetResults";

/**
 * A practice set's results opened from a course's learning report, for an
 * administrator (the author reads them in the set editor). Back leads to the
 * report it came from.
 */
export default function SetResultsPage() {
  const params = useSearchParams();
  const setId = params?.get("set") ?? "";
  const course = params?.get("course") ?? "";
  const { t } = useTranslation();
  if (!setId) return <p className="text-[13px] text-[var(--muted-foreground)]">{t("Not found.")}</p>;
  return (
    <article className="space-y-5" data-testid="set-results-page">
      {course && (
        <Link href={`/reports?kind=course&id=${encodeURIComponent(course)}`}
          className="inline-flex items-center gap-1.5 text-[12.5px] text-[var(--muted-foreground)] hover:text-[var(--foreground)]">
          <ArrowLeft size={14} aria-hidden />
          {t("Learning report")}
        </Link>
      )}
      <h1 className={PAGE_TITLE_CLASS}>{t("Practice set results")}</h1>
      <SetResults key={setId} setId={setId} initiallyOpen />
    </article>
  );
}
