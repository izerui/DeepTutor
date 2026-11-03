"use client";

import Link from "next/link";
import { useTranslation } from "react-i18next";

import { PageHeader, pageActionClass } from "@/components/layout/FeaturePage";

/** Platform batch 2 §3: classes are retired; old links to /classes land here. */
export default function ClassesRetired() {
  const { t } = useTranslation();
  return (
    <PageHeader
      title={t("Classes are no longer used")}
      description={t(
        "Learning now happens through the learning market: anyone can join books, knowledge bases, Mastery Paths, practice sets and courses.",
      )}
      action={<Link href="/market" className={pageActionClass()}>{t("Go to the learning market")}</Link>}
    />
  );
}
