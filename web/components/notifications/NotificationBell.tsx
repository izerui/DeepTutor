"use client";

import { Bell } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  listNotifications,
  markNotificationsRead,
  unreadNotifications,
  type AppNotification,
} from "@/lib/notifications-api";

const POLL_MS = 60_000;

type Translate = (key: string, values?: Record<string, unknown>) => string;

const TRANSFERRED: [string, string][] = [
  ["tasks", "{{count}} tasks"],
  ["courses", "{{count}} courses"],
  ["practice_sets", "{{count}} practice sets"],
  ["teacher_paths", "{{count}} Mastery Paths"],
  ["books", "{{count}} books"],
  ["knowledge_bases", "{{count}} knowledge bases"],
];

/** Batch 4 §3: one summary of everything a departing teacher handed over. */
function transferredText(p: Record<string, unknown>, t: Translate): string {
  const parts = TRANSFERRED.filter(([key]) => Number(p[key]) > 0).map(([key, label]) => t(label, { count: p[key] }));
  return p.from
    ? t("{{from}}'s content was handed over to you: {{items}}", { from: p.from, items: parts.join(", ") })
    : t("Content was handed over to you: {{items}}", { items: parts.join(", ") });
}

/** One line per notification, in the reader's language (the server stores no text). */
export function notificationText(item: AppNotification, t: Translate): string {
  const p = item.params;
  const title = String(p.title ?? "");
  switch (item.kind) {
    case "task_published":
      return t("{{teacher}} assigned “{{title}}”", { teacher: p.teacher, title });
    case "content_published":
      return p.content === "mastery_path"
        ? t("{{teacher}} published the Mastery Path “{{title}}”", { teacher: p.teacher, title })
        : t("{{teacher}} published the practice set “{{title}}”", { teacher: p.teacher, title });
    // Platform batch 3 §3: an announcement in a course or a Mastery Path one joined.
    case "content_announcement":
      return t("{{teacher}} posted an announcement in “{{title}}”", { teacher: p.teacher, title });
    case "course_published":
      return t("{{teacher}} published the course “{{title}}”", { teacher: p.teacher, title });
    case "practice_regraded":
      return t("Your score on “{{title}}” was changed", { title });
    case "material_withdrawn":
      // Batch 3 §6: the material was withdrawn, made private or deleted; count is all learners.
      return t("Material “{{material}}” cited by “{{title}}” is no longer available ({{count}} learners affected)", {
        material: p.material, title, count: p.count,
      });
    case "content_transferred":
      return transferredText(p, t);
    case "tasks_transferred":
      return t("{{count}} tasks were handed over to you", { count: p.count });
    // Platform batch 1 §2: the listing's way through review.
    case "listing_submitted":
      return t("“{{title}}” is waiting for review", { title });
    case "listing_approved":
      return t("“{{title}}” is in the market", { title });
    case "listing_rejected":
      return t("“{{title}}” was not approved: {{reason}}", { title, reason: p.reason });
    case "listing_taken_down":
      return t("“{{title}}” was taken out of the market: {{reason}}", { title, reason: p.reason });
    // Follow-ups §1: new reviews of one's item, at most one notification a day (counted).
    case "listing_reviewed":
      return (item.count ?? 1) > 1
        ? t("{{count}} new reviews of “{{title}}”", { title, count: item.count })
        : t("Someone reviewed “{{title}}”", { title });
    default:
      return item.kind;
  }
}

/**
 * In-app notifications (spec 2026-11-03 batch 1 §5): unread count, a list,
 * all read at once. Opening one goes where it points; that page checks access.
 */
export function NotificationBell({ collapsed = false }: { collapsed?: boolean }) {
  const { t } = useTranslation();
  const router = useRouter();
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<AppNotification[] | null>(null);
  const box = useRef<HTMLDivElement | null>(null);

  const refreshCount = useCallback(() => {
    unreadNotifications().then(setUnread).catch(() => undefined);
  }, []);

  useEffect(() => {
    refreshCount();
    const timer = window.setInterval(refreshCount, POLL_MS);
    return () => window.clearInterval(timer);
  }, [refreshCount]);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    listNotifications()
      .then((data) => {
        if (!alive) return;
        setItems(data.notifications);
        setUnread(data.unread);
      })
      .catch(() => alive && setItems([]));
    const close = (event: MouseEvent) => {
      if (box.current && !box.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => {
      alive = false;
      document.removeEventListener("mousedown", close);
    };
  }, [open]);

  const openItem = (item: AppNotification) => {
    setOpen(false);
    if (!item.read) {
      markNotificationsRead([item.id]).then(setUnread).catch(() => undefined);
    }
    if (item.link) router.push(item.link);
  };

  return (
    <div ref={box} className="relative">
      <button
        type="button"
        aria-label={unread ? t("Notifications ({{count}} unread)", { count: unread }) : t("Notifications")}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="relative flex h-8 w-8 items-center justify-center rounded-md text-foreground/85 hover:bg-background/60 hover:text-[var(--foreground)]"
      >
        <Bell size={collapsed ? 18 : 15} strokeWidth={1.6} />
        {unread > 0 && (
          <span className="absolute -right-0.5 -top-0.5 min-w-4 rounded-full bg-red-500 px-1 text-center text-[10px] font-semibold leading-4 text-white">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>
      {open && (
        <div
          role="dialog"
          aria-label={t("Notifications")}
          className={`absolute z-50 w-80 max-w-[85vw] rounded-xl border border-[var(--border)] bg-[var(--popover)] p-2 shadow-xl ${
            collapsed ? "bottom-0 left-full ml-2" : "bottom-full left-0 mb-2"
          }`}
        >
          <div className="flex items-center justify-between px-2 py-1">
            <span className="text-[13px] font-medium">{t("Notifications")}</span>
            {unread > 0 && (
              <button
                type="button"
                className="text-[12px] text-[var(--primary)] hover:underline"
                onClick={() => {
                  markNotificationsRead()
                    .then((count) => {
                      setUnread(count);
                      setItems((current) => current?.map((item) => ({ ...item, read: true })) ?? null);
                    })
                    .catch(() => undefined);
                }}
              >
                {t("Mark all read")}
              </button>
            )}
          </div>
          {items === null ? (
            <p role="status" className="px-2 py-3 text-[12px] text-[var(--muted-foreground)]">{t("Loading…")}</p>
          ) : items.length === 0 ? (
            <p className="px-2 py-3 text-[12px] text-[var(--muted-foreground)]">{t("No notifications yet.")}</p>
          ) : (
            <ul className="max-h-96 space-y-0.5 overflow-y-auto">
              {items.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    onClick={() => openItem(item)}
                    className={`w-full rounded-lg px-2 py-1.5 text-left text-[12.5px] hover:bg-[var(--muted)] ${
                      item.read ? "text-[var(--muted-foreground)]" : "font-medium text-[var(--foreground)]"
                    }`}
                  >
                    {notificationText(item, t)}
                    <span className="block text-[11px] font-normal text-[var(--muted-foreground)]">
                      {new Date(item.created_at * 1000).toLocaleString()}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
