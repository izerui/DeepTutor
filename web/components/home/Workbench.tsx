"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { usePendingReviews } from "@/components/catalog/AdminNav";
import { MyListings } from "@/components/catalog/MyListings";
import { flagText } from "@/components/catalog/attention-format";
import { MASTERY_HOME, masteryTopicRoute } from "@/lib/learning-routes";
import { getHome, type HomeData } from "@/lib/tasks-api";
import { getWorkbench, type Workbench } from "@/lib/workbench-api";

import { AiLimitSetting } from "./AiUsage";
import { HomeAnnouncements, JoinedSection } from "./StudentHome";

const card = "rounded-xl border border-[var(--border)] p-3 transition-colors hover:border-[var(--ring)]";
const SENT_KIND: Record<string, string> = {
  task: "Task",
  practice_set: "Practice set",
  course: "Course",
  mastery_path: "Mastery Path",
};
const sentHref = (kind: string, id: string) =>
  kind === "task" ? `/tasks?task=${encodeURIComponent(id)}`
    : kind === "practice_set" ? `/practice-sets?set=${encodeURIComponent(id)}`
      : kind === "course" ? "/tasks" : MASTERY_HOME;

/** The home data (own learning, what one joined, announcements), read once per page. */
function useHome(): HomeData | null {
  const [home, setHome] = useState<HomeData | null>(null);
  useEffect(() => {
    getHome().then(setHome).catch(() => setHome(null));
  }, []);
  return home;
}

/** The teacher's and the administrator's own learning, the student home's "mine" block. */
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

/** The teacher's home (spec 2026-11-03 batch 3 §5.1). */
export function TeacherWorkbench() {
  const { t } = useTranslation();
  const home = useHome();
  const [board, setBoard] = useState<Workbench | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    getWorkbench().then(setBoard).catch((err) => setError(err instanceof Error ? t(err.message) : String(err)));
  }, [t]);

  return (
    <div className="mx-auto max-w-5xl space-y-8 px-6 py-8" data-testid="teacher-workbench">
      <header>
        <h1 className="font-serif text-[27px] text-[var(--foreground)]">{t("Home")}</h1>
        <p className="mt-1 text-[13px] text-[var(--muted-foreground)]">
          {t("What waits for you, who needs attention, what you sent out and listed, and your own learning.")}
        </p>
      </header>
      {error && <p role="alert" className="text-[12px] text-red-600">{error}</p>}
      {/* Traceability #6: announcements of what one joined, as on the student home. */}
      <HomeAnnouncements items={home?.announcements ?? []} />
      {!board ? (
        <div aria-busy="true" className="h-40 animate-pulse rounded-xl bg-[var(--muted)]" />
      ) : (
        <>
          <section aria-labelledby="wb-grade" className="space-y-2">
            <div className="flex items-center justify-between">
              <h2 id="wb-grade" className="text-[13px] font-semibold">
                {t("To grade")} <span className="text-[var(--muted-foreground)]">({board.to_grade?.length ?? 0})</span>
              </h2>
              <Link href="/grading" className="text-[12.5px] text-[var(--primary)] hover:underline">{t("All hand-ins")}</Link>
            </div>
            {(board.to_grade ?? []).length === 0 ? (
              <p className="text-[12.5px] text-[var(--muted-foreground)]">{t("Nothing to grade.")}</p>
            ) : (
              <ul className="divide-y divide-[var(--border)] rounded-xl border border-[var(--border)]">
                {(board.to_grade ?? []).slice(0, 5).map((item) => (
                  <li key={`${item.task_id}:${item.student_id}`}>
                    <Link href={`/tasks?task=${encodeURIComponent(item.task_id)}`} className="flex flex-wrap items-center gap-2 p-2.5 text-[12.5px] hover:bg-[var(--muted)]/40">
                      <span className="font-medium">{item.username}</span>
                      <span className="text-[var(--muted-foreground)]">{item.task_title}</span>
                      <span className="ml-auto text-[11.5px] text-[var(--muted-foreground)]">
                        {new Date(item.submitted_at * 1000).toLocaleString()}
                        {item.late && ` · ${t("Late")}`}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* Platform batch 3 §2: learners of my courses and paths; each opens that item's report. */}
          <section aria-labelledby="wb-attention" className="space-y-2">
            <h2 id="wb-attention" className="text-[13px] font-semibold">
              {t("Learners who need attention")}{" "}
              <span className="text-[var(--muted-foreground)]">({board.attention_total ?? 0})</span>
            </h2>
            {(board.attention ?? []).length === 0 ? (
              <p className="text-[12.5px] text-[var(--muted-foreground)]">{t("Everyone is on track.")}</p>
            ) : (
              <ul className="divide-y divide-[var(--border)] rounded-xl border border-[var(--border)]">
                {(board.attention ?? []).map((row) => {
                  const body = (
                    <>
                      <span className="font-medium">{row.username}</span>
                      {row.item && <span className="text-[11.5px] text-[var(--muted-foreground)]">{row.item.title}</span>}
                      <span className="text-[11.5px] text-amber-700 dark:text-amber-300">
                        {row.flags.map((flag) => flagText(flag, t)).join(" · ")}
                      </span>
                    </>
                  );
                  const rowClass = "flex flex-wrap items-baseline gap-2 p-2.5 text-[12.5px]";
                  return (
                    <li key={`${row.student_id}:${row.item?.kind ?? ""}:${row.item?.id ?? ""}`}>
                      {row.item ? (
                        <Link href={`/reports?kind=${encodeURIComponent(row.item.kind)}&id=${encodeURIComponent(row.item.id)}`}
                          className={`${rowClass} hover:bg-[var(--muted)]/40`}>
                          {body}
                        </Link>
                      ) : (
                        <div className={rowClass}>{body}</div>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          <section aria-labelledby="wb-sent" className="space-y-2">
            <h2 id="wb-sent" className="text-[13px] font-semibold">{t("What I sent out")}</h2>
            {(board.sent ?? []).length === 0 ? (
              <p className="text-[12.5px] text-[var(--muted-foreground)]">{t("Nothing published yet.")}</p>
            ) : (
              <ul className="space-y-1.5">
                {(board.sent ?? []).map((item) => (
                  <li key={`${item.kind}:${item.id}`}>
                    <Link href={sentHref(item.kind, item.id)} className="flex items-center gap-3 rounded-lg px-2 py-1.5 text-[12.5px] hover:bg-[var(--muted)]/40">
                      <span className="w-24 shrink-0 text-[11px] text-[var(--muted-foreground)]">{t(SENT_KIND[item.kind])}</span>
                      <span className="min-w-0 flex-1 truncate font-medium">{item.title}</span>
                      {item.done === null ? (
                        <span className="text-[11.5px] text-[var(--muted-foreground)]">
                          {item.kind === "course" ? t("{{count}} tasks", { count: item.total }) : t("{{count}} students", { count: item.total })}
                        </span>
                      ) : (
                        <span className="flex items-center gap-2 text-[11.5px] text-[var(--muted-foreground)]">
                          <span className="h-1.5 w-20 overflow-hidden rounded-full bg-[var(--muted)]">
                            <span className="block h-full bg-[var(--primary)]" style={{ width: `${item.total ? Math.round((100 * item.done) / item.total) : 0}%` }} />
                          </span>
                          {t("{{done}} / {{total}} done", { done: item.done, total: item.total })}
                        </span>
                      )}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
      <MyListings />
      <JoinedSection items={home?.joined ?? []} />
      <MyLearning home={home} />
    </div>
  );
}

/** The administrator's home: the ways in, and their own learning (§5.2) — no students' data. */
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
      {/* Traceability #5: administrators do not grade; what they hold goes on to a teacher. */}
      <p className="text-[12px] text-[var(--muted-foreground)]" data-testid="admin-hand-over-hint">
        {t("Courses and tasks handed to you can be handed to a teacher who grades them.")}{" "}
        <Link href="/tasks?tab=courses" className="text-[var(--primary)] hover:underline">{t("Courses")}</Link>
      </p>
      <AiLimitSetting />
      <MyListings />
      <JoinedSection items={home?.joined ?? []} />
      <MyLearning home={home} />
    </div>
  );
}
