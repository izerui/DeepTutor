"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { SourceBadge } from "@/components/common/SourceBadge";
import { StudentTaskDetail } from "@/components/tasks/StudentTaskDetail";
import { AiUsageNote } from "./AiUsage";
import { formatWhen, KIND_LABEL, STATE_LABEL, STATE_TONE } from "@/components/tasks/task-format";
import { bookRoute, MASTERY_HOME, masteryTopicRoute } from "@/lib/learning-routes";
import { getAssignedTask, getHome, listAssignedTasks, type AssignedTask, type HomeData } from "@/lib/tasks-api";

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
        {task.due_at ? t("Due {{date}}", { date: formatWhen(task.due_at) }) : ""}
      </span>
    </button>
  );
}

/**
 * The student home (spec 2026-11-03 batch 1 §6): what teachers assigned,
 * what the school published, and one's own learning — never mixed.
 */
export default function StudentHome() {
  const { t } = useTranslation();
  const [home, setHome] = useState<HomeData | null>(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<AssignedTask | null>(null);
  const [history, setHistory] = useState<AssignedTask[] | null>(null);

  const refresh = useCallback(() => {
    getHome()
      .then(setHome)
      .catch((err) => setError(err instanceof Error ? t(err.message) : String(err)));
  }, [t]);
  useEffect(refresh, [refresh]);

  // ``?task=`` (a notification's link) opens one task directly, also when already here.
  const taskParam = useSearchParams().get("task");
  useEffect(() => {
    const key = taskParam;
    if (!key) return;
    getAssignedTask(key)
      .then(setSelected)
      .catch(() => setError(t("This task is no longer available.")));
  }, [t, taskParam]);

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
            {t("What your teachers assigned, what the school published, and what you are learning yourself.")}
          </p>
        </div>
        <Link href="/chat" className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-[13px]">
          {t("Start a conversation")}
        </Link>
      </header>
      {error && <p role="alert" className="text-[12px] text-red-600">{error}</p>}
      <AiUsageNote />
      {selected && (
        <StudentTaskDetail
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
          {(home.announcements ?? []).length > 0 && (
            <section aria-labelledby="home-announcements" className="space-y-2">
              <h2 id="home-announcements" className="text-[13px] font-semibold">{t("Class announcements")}</h2>
              <ul className="space-y-2">
                {(home.announcements ?? []).map((item) => (
                  <li key={item.id} className="rounded-xl border border-[var(--border)] p-3 text-[12.5px]">
                    <p className="whitespace-pre-line text-[var(--foreground)]">{item.text}</p>
                    <p className="mt-1 text-[11px] text-[var(--muted-foreground)]">
                      {item.class_name} · {item.author || t("Former teacher")} · {formatWhen(item.created_at)}
                    </p>
                  </li>
                ))}
              </ul>
              <Link href="/classes" className="text-[12.5px] text-[var(--primary)] hover:underline">
                {t("All announcements")}
              </Link>
            </section>
          )}
          <section aria-labelledby="home-assigned" className="space-y-3">
            <h2 id="home-assigned" className="text-[13px] font-semibold">{t("From my teachers")}</h2>
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

          {home.school.length > 0 && (
            <section aria-labelledby="home-school" className="space-y-3">
              <h2 id="home-school" className="text-[13px] font-semibold">{t("Published by the school")}</h2>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {home.school.map((item) => (
                  <Link
                    key={`${item.kind}:${item.id}`}
                    href={item.kind === "book" ? bookRoute(`book:${item.id}`, null, "") : "/knowledge-bases"}
                    className={card}
                  >
                    <span className="text-[11px] text-[var(--muted-foreground)]">
                      {item.kind === "book" ? t("Book") : t("Knowledge base")}
                    </span>
                    <span className="mt-1 block truncate text-[14px] font-medium">{item.title}</span>
                  </Link>
                ))}
              </div>
            </section>
          )}

          <section aria-labelledby="home-mine" className="space-y-3">
            <div className="flex items-center justify-between">
              <h2 id="home-mine" className="text-[13px] font-semibold">{t("What I am learning")}</h2>
              {home.can_create_path && (
                <Link href={MASTERY_HOME} className="text-[12.5px] text-[var(--primary)] hover:underline">
                  {t("New Mastery Path")}
                </Link>
              )}
            </div>
            {home.mine.length === 0 && (home.mine_courses ?? []).length === 0 ? (
              <p className="text-[12.5px] text-[var(--muted-foreground)]">{t("You have not started anything of your own yet.")}</p>
            ) : (
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                {(home.mine_courses ?? []).map((course) => (
                  <Link key={course.id} href={`/courses/${encodeURIComponent(course.id)}`} className={card}>
                    <span className="block truncate text-[14px] font-medium">{course.name}</span>
                    <span className="text-[11.5px] text-[var(--muted-foreground)]">{t("Course")}</span>
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
