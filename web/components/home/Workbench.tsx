"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { usePendingReviews } from "@/components/catalog/AdminNav";
import { MyListings } from "@/components/catalog/MyListings";
import { MASTERY_HOME, masteryTopicRoute } from "@/lib/learning-routes";
import { getHome, type HomeData } from "@/lib/tasks-api";

import { AiLimitSetting } from "./AiUsage";
import { HomeAnnouncements, JoinedSection } from "./LearnerHome";

const card = "rounded-xl border border-[var(--border)] p-3 transition-colors hover:border-[var(--ring)]";
/** The home data (own learning, what one joined, announcements), read once per page. */
function useHome(): HomeData | null {
  const [home, setHome] = useState<HomeData | null>(null);
  useEffect(() => {
    getHome().then(setHome).catch(() => setHome(null));
  }, []);
  return home;
}

/** The administrator's own learning, the learner home's "mine" block. */
function MyLearning({ home }: { home: HomeData | null }) {
  const { t } = useTranslation();
  return (
    <section aria-labelledby="home-mine" className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 id="home-mine" className="text-[13px] font-semibold">{t("What I am learning")}</h2>
        <Link href={MASTERY_HOME} className="text-[12.5px] text-[var(--primary)] hover:underline">{t("New Mastery Path")}</Link>
      </div>
      {!home || home.mine.length === 0 ? (
        <p className="text-[12.5px] text-[var(--muted-foreground)]">{t("You have not started anything of your own yet.")}</p>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {home.mine.map((path) => (
            <Link key={path.path_id} href={masteryTopicRoute(path.path_id, "")} className={card}>
              <span className="block truncate text-[14px] font-medium">{path.name}</span>
              <span className="text-[11.5px] text-[var(--muted-foreground)]">
                {t("{{mastered}} / {{total}} mastered", { mastered: path.mastered, total: path.total })}
              </span>
            </Link>
          ))}
        </div>
      )}
    </section>
  );
}

/** The administrator's home: the ways in, and their own learning (§5.2). */
export function AdminHome() {
  const { t } = useTranslation();
  const links: [string, string][] = [
    ["/admin/users", "Accounts"],
    ["/admin/catalog", "Classification and tags"],
    ["/settings", "Models and settings"],
  ];
  const pending = usePendingReviews();
  const home = useHome();
  return (
    <div className="mx-auto max-w-5xl space-y-8 px-6 py-8" data-testid="admin-home">
      <h1 className="font-serif text-[27px] text-[var(--foreground)]">{t("Home")}</h1>
      <HomeAnnouncements items={home?.announcements ?? []} />
      <div className="grid gap-3 sm:grid-cols-3">
        {links.map(([href, label]) => (
          <Link key={href} href={href} className={card}>
            <span className="block text-[13px] font-medium">{t(label)}</span>
          </Link>
        ))}
        {/* Platform batch 1 §5.2: what waits for review. */}
        <Link href="/admin/review" className={card} data-testid="admin-pending-reviews">
          <span className="block text-[13px] font-medium">{t("Listing review")}</span>
          <span className="text-[11.5px] text-[var(--muted-foreground)]">
            {t("{{count}} waiting for review", { count: pending ?? 0 })}
          </span>
        </Link>
      </div>
      {/* Traceability #5: what administrators hold can go on to a learner. */}
      <p className="text-[12px] text-[var(--muted-foreground)]" data-testid="admin-hand-over-hint">
        {t("Courses and tasks you hold can be handed to a learner.")}{" "}
        <Link href="/tasks?tab=courses" className="text-[var(--primary)] hover:underline">{t("Courses")}</Link>
      </p>
      <AiLimitSetting />
      <MyListings />
      <JoinedSection items={home?.joined ?? []} />
      <MyLearning home={home} />
    </div>
  );
}
