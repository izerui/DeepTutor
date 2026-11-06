"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { MyListings } from "@/components/catalog/MyListings";
import { SourceBadge } from "@/components/common/SourceBadge";
import { LearnerLessonDetail } from "@/components/tasks/LearnerLessonDetail";
import { AiUsageNote } from "./AiUsage";
import { authorTaskHref, isContentKey } from "./task-links";
import { formatWhen, KIND_LABEL, STATE_LABEL, STATE_TONE } from "@/components/tasks/task-format";
import { authorLabel, itemHref, kindLabel, studyHref } from "@/components/catalog/catalog-format";
import { useFeatured } from "@/components/catalog/FeaturedRows";
import type { CatalogEntry, HomeAnnouncement } from "@/lib/catalog-api";
import { MASTERY_HOME, masteryTopicRoute } from "@/lib/learning-routes";
import {
  getAssignedTask,
  getHome,
  listAssignedTasks,
  listMyTasks,
  type AssignedTask,
  type HomeData,
  type JoinedItem,
} from "@/lib/tasks-api";
import { getWorkbench, type Workbench } from "@/lib/workbench-api";

type HomeMarketItem = Pick<CatalogEntry, "kind" | "id" | "title" | "author" | "author_role">;

const card = "rounded-xl border border-[var(--border)] p-3 text-left transition-colors hover:border-[var(--ring)]";

function TaskCard({ task, onOpen, selected }: { task: AssignedTask; onOpen: () => void; selected: boolean }) {
  const { t } = useTranslation();
  return (
    <button type="button" onClick={onOpen} className={`${card} w-full ${selected ? "border-[var(--ring)]" : ""}`}>
      <span className="flex flex-wrap items-center gap-1.5">
        <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${STATE_TONE[task.state]}`}>
          {t(STATE_LABEL[task.state])}
        </span>
        <span className="text-[11px] text-[var(--muted-foreground)]">{t(KIND_LABEL[task.kind])}</span>
      </span>
      <span className="mt-1 block truncate text-[14px] font-medium text-[var(--foreground)]">{task.title}</span>
      <span className="mt-1 flex flex-wrap items-center gap-1.5 text-[11.5px] text-[var(--muted-foreground)]">
        <SourceBadge provenance={task.provenance} />
      </span>
    </button>
  );
}

/** Where an announcement's item opens for a learner: the course, or the path's intro. */
export function announcementHref(item: HomeAnnouncement): string | null {
  if (!item.item_id) return null;
  if (item.kind === "course") return `/tasks?course=${encodeURIComponent(item.item_id)}`;
  if (item.kind === "mastery_path") return itemHref("mastery_path", item.item_id);
  return null;
}

/** Platform batch 3 §3: the newest announcements of the courses and paths one joined. */
export function HomeAnnouncements({ items }: { items: HomeAnnouncement[] }) {
  const { t } = useTranslation();
  if (!items.length) return null;
  return (
    <section aria-labelledby="home-announcements" className="space-y-2">
      <h2 id="home-announcements" className="text-[13px] font-semibold">{t("Announcements")}</h2>
      <ul className="space-y-2">
        {items.map((item) => {
          const title = item.item_title || item.class_name || "";
          const href = announcementHref(item);
          return (
            <li key={item.id} className="rounded-xl border border-[var(--border)] p-3 text-[12.5px]">
              <p className="whitespace-pre-line text-[var(--foreground)]">{item.text}</p>
              <p className="mt-1 text-[11px] text-[var(--muted-foreground)]">
                {href ? (
                  <Link href={href} className="text-[var(--primary)] hover:underline">{title}</Link>
                ) : (
                  title
                )}
                {" · "}{item.author || t("Former author")} · {formatWhen(item.created_at)}
              </p>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** Traceability #6: what one joined from the learning market; each opens where it is studied. */
export function JoinedSection({ items }: { items: JoinedItem[] }) {
  const { t } = useTranslation();
  if (!items.length) return null;
  return (
    <section aria-labelledby="home-joined" className="space-y-3" data-testid="home-joined">
      <div className="flex items-center justify-between">
        <h2 id="home-joined" className="text-[13px] font-semibold">{t("What I joined")}</h2>
        <Link href="/market" className="text-[12.5px] text-[var(--primary)] hover:underline">{t("Learning market")}</Link>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {items.map((item) => (
          <Link key={`${item.kind}:${item.id}`} href={studyHref(item.study)} className={card}>
            <span className="block truncate text-[14px] font-medium">{item.title}</span>
            <span className="text-[11.5px] text-[var(--muted-foreground)]">{kindLabel(item.kind, t)}</span>
          </Link>
        ))}
      </div>
    </section>
  );
}

const SENT_KIND: Record<string, string> = {
  task: "Lesson",
  practice_set: "Practice set",
  course: "Course",
  mastery_path: "Mastery Path",
};
const sentHref = (kind: string, id: string) =>
  kind === "task" ? `/tasks?task=${encodeURIComponent(id)}`
    : kind === "practice_set" ? `/practice-sets?set=${encodeURIComponent(id)}`
      : kind === "course" ? "/tasks?tab=courses" : MASTERY_HOME;

/** What one sent out, with how far its learners are (spec 2026-11-05: every learner's home). */
export function SentOut() {
  const { t } = useTranslation();
  const [board, setBoard] = useState<Workbench | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    getWorkbench().then(setBoard).catch((err) => setError(err instanceof Error ? t(err.message) : String(err)));
  }, [t]);
  if (error) return <p role="alert" className="text-[12px] text-red-600">{error}</p>;
  if (!board) return <div aria-busy="true" className="h-24 animate-pulse rounded-xl bg-[var(--muted)]" />;
  const sent = board.sent ?? [];
  return (
    <section aria-labelledby="wb-sent" className="space-y-2" data-testid="home-sent">
      <h2 id="wb-sent" className="text-[13px] font-semibold">{t("What I sent out")}</h2>
      {sent.length === 0 ? (
        <p className="text-[12.5px] text-[var(--muted-foreground)]">{t("Nothing published yet.")}</p>
      ) : (
        <ul className="space-y-1.5">
          {sent.map((item) => (
            <li key={`${item.kind}:${item.id}`}>
              <Link href={sentHref(item.kind, item.id)} className="flex items-center gap-3 rounded-lg px-2 py-1.5 text-[12.5px] hover:bg-[var(--muted)]/40">
                <span className="w-24 shrink-0 text-[11px] text-[var(--muted-foreground)]">{t(SENT_KIND[item.kind])}</span>
                <span className="min-w-0 flex-1 truncate font-medium">{item.title}</span>
                {item.done === null ? (
                  <span className="text-[11.5px] text-[var(--muted-foreground)]">
                    {item.kind === "course" ? t("{{count}} lessons", { count: item.total }) : t("{{count}} learners", { count: item.total })}
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
  );
}

function MarketCards({ id, title, items }: { id: string; title: string; items: HomeMarketItem[] }) {
  const { t } = useTranslation();
  return (
    <section aria-labelledby={id} className="space-y-3" data-testid={id}>
      <div className="flex items-center justify-between">
        <h2 id={id} className="text-[13px] font-semibold">{title}</h2>
        <Link href="/market" className="text-[12.5px] text-[var(--primary)] hover:underline">{t("Learning market")}</Link>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {items.map((item) => (
          <Link key={`${item.kind}:${item.id}`} href={itemHref(item.kind, item.id)} className={card}>
            <span className="text-[11px] text-[var(--muted-foreground)]">{kindLabel(item.kind, t)}</span>
            <span className="mt-1 block truncate text-[14px] font-medium">{item.title}</span>
            <span className="block truncate text-[11.5px] text-[var(--muted-foreground)]">{authorLabel(item, t)}</span>
          </Link>
        ))}
      </div>
    </section>
  );
}

/** What is new in the market and, beside it, what most people learn (spec 2026-11-06 §3); each hidden when empty. */
export function MarketRows({ recent, popular }: { recent: HomeMarketItem[]; popular: HomeMarketItem[] }) {
  const { t } = useTranslation();
  if (recent.length === 0 && popular.length === 0) return null;
  return (
    <div className="space-y-6">
      {recent.length > 0 && <MarketCards id="home-market" title={t("New in the market")} items={recent} />}
      {popular.length > 0 && <MarketCards id="home-popular" title={t("Most popular")} items={popular.slice(0, 6)} />}
    </div>
  );
}

/**
 * Every learner's home (spec 2026-11-03 batch 1 §6; spec 2026-11-05): tasks from
 * the courses one joined, what one sent out and listed, what is new in the
 * learning market, and one's own learning — never mixed.
 */
export default function LearnerHome() {
  const { t } = useTranslation();
  const [home, setHome] = useState<HomeData | null>(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<AssignedTask | null>(null);
  const [history, setHistory] = useState<AssignedTask[] | null>(null);
  const popular = useFeatured()?.popular ?? [];

  const refresh = useCallback(() => {
    getHome()
      .then(setHome)
      .catch((err) => setError(err instanceof Error ? t(err.message) : String(err)));
  }, [t]);
  useEffect(refresh, [refresh]);

  // ``?task=`` (a notification's link) opens one task directly, also when already here.
  const taskParam = useSearchParams().get("task");
  const router = useRouter();
  useEffect(() => {
    const key = taskParam;
    if (!key) return;
    getAssignedTask(key)
      .then(setSelected)
      .catch(async () => {
        // Older notifications named a practice set or a path as a task; open it directly.
        if (isContentKey(key)) return router.replace(authorTaskHref(key, false));
        // A task one sent out opens in its editor (traceability #7).
        const own = await listMyTasks().then((rows) => rows.some((row) => row.id === key)).catch(() => false);
        if (own) router.replace(authorTaskHref(key, true));
        else setError(t("This lesson is no longer available."));
      });
  }, [t, taskParam, router]);

  const showHistory = () =>
    listAssignedTasks()
      .then((rows) => setHistory(rows.filter((row) => row.state === "completed" || row.state === "closed")))
      .catch((err) => setError(err instanceof Error ? t(err.message) : String(err)));

  return (
    <div className="mx-auto max-w-5xl space-y-8 px-6 py-8">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-serif text-[27px] text-[var(--foreground)]">{t("Home")}</h1>
          <p className="mt-1 text-[13px] text-[var(--muted-foreground)]">
            {t("Lessons from your courses, what is new in the market, and what you are learning yourself.")}
          </p>
        </div>
        <Link href="/chat" className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-[13px]">
          {t("Start a conversation")}
        </Link>
      </header>
      {error && <p role="alert" className="text-[12px] text-red-600">{error}</p>}
      <AiUsageNote />
      {selected && (
        <LearnerLessonDetail
          key={selected.key}
          task={selected}
          onChanged={(next) => {
            setSelected(next);
            refresh();
          }}
        />
      )}
      {!home ? (
        <div aria-busy="true" className="h-40 animate-pulse rounded-xl bg-[var(--muted)]" />
      ) : (
        <>
          <HomeAnnouncements items={home.announcements ?? []} />
          <section aria-labelledby="home-assigned" className="space-y-3">
            <h2 id="home-assigned" className="text-[13px] font-semibold">{t("From my courses")}</h2>
            {home.assigned.length === 0 ? (
              <p className="text-[12.5px] text-[var(--muted-foreground)]">{t("Nothing to do right now.")}</p>
            ) : (
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {home.assigned.map((task) => (
                  <TaskCard key={task.key} task={task} selected={selected?.key === task.key} onOpen={() => setSelected(task)} />
                ))}
              </div>
            )}
            {home.history_count > 0 && history === null && (
              <button type="button" className="text-[12.5px] text-[var(--primary)] hover:underline" onClick={() => void showHistory()}>
                {t("History ({{count}})", { count: home.history_count })}
              </button>
            )}
            {history && (
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" aria-label={t("History")}>
                {history.map((task) => (
                  <TaskCard key={task.key} task={task} selected={selected?.key === task.key} onOpen={() => setSelected(task)} />
                ))}
              </div>
            )}
          </section>

          <SentOut />
          <MyListings />

          <JoinedSection items={home.joined ?? []} />

          <MarketRows recent={home.market ?? []} popular={popular} />

          <section aria-labelledby="home-mine" className="space-y-3">
            <div className="flex items-center justify-between">
              <h2 id="home-mine" className="text-[13px] font-semibold">{t("What I am learning")}</h2>
              <Link href={MASTERY_HOME} className="text-[12.5px] text-[var(--primary)] hover:underline">
                {t("New Mastery Path")}
              </Link>
            </div>
            {home.mine.length === 0 && (home.mine_courses ?? []).length === 0 ? (
              <p className="text-[12.5px] text-[var(--muted-foreground)]">{t("You have not started anything of your own yet.")}</p>
            ) : (
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                {(home.mine_courses ?? []).map((course) => (
                  <Link key={course.id} href={`/courses/${encodeURIComponent(course.id)}`} className={card}>
                    <span className="block truncate text-[14px] font-medium">{course.name}</span>
                    <span className="text-[11.5px] text-[var(--muted-foreground)]">{t("Personal subject label")}</span>
                  </Link>
                ))}
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
        </>
      )}
    </div>
  );
}
