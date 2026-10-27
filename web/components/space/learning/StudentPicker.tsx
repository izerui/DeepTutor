"use client";

import { useEffect, useId, useState } from "react";
import { Loader2, Search } from "lucide-react";
import { useTranslation } from "react-i18next";

import { searchStudents, type StudentOption } from "@/lib/teaching-api";

export function StudentPicker({
  selected,
  onChange,
  exclude = [],
  disabled = false,
}: {
  selected: string[];
  onChange: (ids: string[]) => void;
  /** Students who already have the path; they are not offered again. */
  exclude?: string[];
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [students, setStudents] = useState<StudentOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const statusId = useId();

  useEffect(() => {
    // Only the newest query may fill the list: an older, slower answer is dropped.
    let alive = true;
    const timer = window.setTimeout(() => {
      setLoading(true);
      setFailed(false);
      searchStudents(query)
        .then((rows) => {
          if (alive) setStudents(rows);
        })
        .catch(() => {
          if (!alive) return;
          setStudents([]);
          setFailed(true);
        })
        .finally(() => {
          if (alive) setLoading(false);
        });
    }, 200);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [query, attempt]);

  const visible = students.filter((student) => !exclude.includes(student.id));
  const toggle = (studentId: string) =>
    onChange(
      selected.includes(studentId)
        ? selected.filter((value) => value !== studentId)
        : [...selected, studentId],
    );

  return (
    <div className="mt-2 rounded-lg border border-[var(--border)]">
      <label className="flex items-center gap-2 border-b border-[var(--border)] px-3 py-2">
        <Search aria-hidden className="h-3.5 w-3.5 text-[var(--muted-foreground)]" />
        <input
          type="search"
          value={query}
          disabled={disabled}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={t("Search students")}
          aria-label={t("Search students")}
          aria-describedby={statusId}
          className="min-w-0 flex-1 bg-transparent text-xs outline-none"
        />
      </label>
      <ul aria-label={t("Students")} aria-busy={loading} className="max-h-48 overflow-y-auto p-1">
        {loading ? (
          <li className="flex justify-center py-3 text-[var(--muted-foreground)]">
            <Loader2 aria-hidden className="h-4 w-4 animate-spin" />
            <span className="sr-only">{t("Loading…")}</span>
          </li>
        ) : failed ? (
          <li className="flex items-center justify-center gap-2 px-2 py-3 text-xs text-red-600">
            {t("Students could not be loaded.")}
            <button
              type="button"
              onClick={() => setAttempt((value) => value + 1)}
              className="rounded-md px-2 py-1 font-medium underline-offset-2 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
            >
              {t("Retry")}
            </button>
          </li>
        ) : visible.length === 0 ? (
          <li className="px-2 py-3 text-center text-xs text-[var(--muted-foreground)]">
            {t("No student accounts found")}
          </li>
        ) : (
          visible.map((student) => (
            <li key={student.id}>
              <label className="flex min-h-9 cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-xs hover:bg-[var(--accent)]">
                <input
                  type="checkbox"
                  disabled={disabled}
                  checked={selected.includes(student.id)}
                  onChange={() => toggle(student.id)}
                />
                <span className="truncate">{student.username}</span>
              </label>
            </li>
          ))
        )}
      </ul>
      <p
        id={statusId}
        aria-live="polite"
        className="border-t border-[var(--border)] px-3 py-1.5 text-[11px] text-[var(--muted-foreground)]"
      >
        {t("{{count}} selected", { count: selected.length })}
      </p>
    </div>
  );
}
