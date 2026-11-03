"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { useAuthStatus } from "@/hooks/useAuthStatus";
import { listCalendar, type CalendarEvent } from "@/lib/tasks-api";

const dayKey = (date: Date) => `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;

/** Openings and deadlines of tasks by month (spec 2026-11-03 batch 2 §7). */
export default function TaskCalendar() {
  const { t, i18n } = useTranslation();
  const { isAdmin, isTeacher } = useAuthStatus();
  const [events, setEvents] = useState<CalendarEvent[] | null>(null);
  const [error, setError] = useState("");
  const [month, setMonth] = useState(() => {
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), 1);
  });

  useEffect(() => {
    listCalendar().then(setEvents).catch((err) => setError(err instanceof Error ? t(err.message) : String(err)));
  }, [t]);

  const byDay = useMemo(() => {
    const map = new Map<string, CalendarEvent[]>();
    for (const event of events ?? []) {
      const key = dayKey(new Date(event.at * 1000));
      map.set(key, [...(map.get(key) ?? []), event]);
    }
    return map;
  }, [events]);

  // Weeks start on Monday; leading blanks fill up to the first day.
  const lead = (month.getDay() + 6) % 7;
  const days = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate();
  const cells = [...Array(lead).fill(null), ...Array.from({ length: days }, (_, i) => new Date(month.getFullYear(), month.getMonth(), i + 1))];
  const today = dayKey(new Date());
  const weekdays = Array.from({ length: 7 }, (_, i) =>
    new Date(2024, 0, 1 + i).toLocaleDateString(i18n.language, { weekday: "short" }),
  );
  const link = (event: CalendarEvent) =>
    isAdmin || isTeacher ? `/tasks?task=${encodeURIComponent(event.key)}` : `/start?task=${encodeURIComponent(event.key)}`;

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-serif text-[27px] text-[var(--foreground)]">{t("Calendar")}</h1>
          <p className="mt-1 text-[13px] text-[var(--muted-foreground)]">{t("When tasks open and when they are due.")}</p>
        </div>
        <div className="flex items-center gap-2">
          <button type="button" aria-label={t("Previous month")} className="rounded-md border border-[var(--border)] p-1"
            onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))}>
            <ChevronLeft size={16} />
          </button>
          <span className="min-w-32 text-center text-[14px] font-medium">
            {month.toLocaleDateString(i18n.language, { year: "numeric", month: "long" })}
          </span>
          <button type="button" aria-label={t("Next month")} className="rounded-md border border-[var(--border)] p-1"
            onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))}>
            <ChevronRight size={16} />
          </button>
        </div>
      </header>
      {error && <p role="alert" className="text-[12px] text-red-600">{error}</p>}
      <div className="grid grid-cols-7 gap-px overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--border)]" data-testid="calendar-grid">
        {weekdays.map((name) => (
          <div key={name} className="bg-[var(--muted)] px-2 py-1 text-[11.5px] font-medium text-[var(--muted-foreground)]">{name}</div>
        ))}
        {cells.map((date, index) => (
          <div key={index} className={`min-h-24 bg-[var(--background)] p-1.5 ${date && dayKey(date) === today ? "ring-1 ring-inset ring-[var(--primary)]" : ""}`}>
            {date && (
              <>
                <span className="text-[11.5px] text-[var(--muted-foreground)]">{date.getDate()}</span>
                <ul className="mt-0.5 space-y-0.5">
                  {(byDay.get(dayKey(date)) ?? []).map((event) => (
                    <li key={`${event.key}:${event.type}`}>
                      <Link href={link(event)}
                        className={`block truncate rounded px-1 py-0.5 text-[11px] ${event.type === "due" ? "bg-amber-100 text-amber-900 dark:bg-amber-900/40 dark:text-amber-100" : "bg-violet-100 text-violet-900 dark:bg-violet-900/40 dark:text-violet-100"}`}>
                        {event.type === "due" ? t("Due") : t("Opens")} · {event.title}
                      </Link>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        ))}
      </div>
      {events !== null && events.length === 0 && (
        <p className="text-[12.5px] text-[var(--muted-foreground)]">{t("No dates yet. Deadlines and opening times of tasks appear here.")}</p>
      )}
    </div>
  );
}
