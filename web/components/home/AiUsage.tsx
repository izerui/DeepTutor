"use client";

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { AI_LIMIT_PRESETS, getMyAiUsage, getSchoolAiLimit, setSchoolAiLimit, type AiUsage } from "@/lib/workbench-api";

/** A student's AI left today (spec 2026-11-03 batch 3 §8); nothing without a limit. */
export function AiUsageNote() {
  const { t } = useTranslation();
  const [usage, setUsage] = useState<AiUsage | null>(null);
  useEffect(() => {
    getMyAiUsage().then(setUsage).catch(() => setUsage(null));
  }, []);
  if (!usage || !usage.limit || usage.remaining_percent === null) return null;
  const low = usage.remaining_percent < 20;
  return (
    <p role="status" data-testid="ai-usage"
      className={`text-[12px] ${low ? "text-amber-700 dark:text-amber-300" : "text-[var(--muted-foreground)]"}`}>
      {usage.remaining_percent === 0
        ? t("Today's AI usage is used up. Come back tomorrow.")
        : t("AI left today: {{percent}}%", { percent: usage.remaining_percent })}
    </p>
  );
}

/** Choosing a daily AI limit: presets shown as conversations, or a number of tokens. */
export function AiLimitPicker({
  value,
  onChange,
  inheritLabel,
}: {
  /** ``undefined``: inherit (only offered with ``inheritLabel``); ``0``: no limit. */
  value: number | undefined;
  onChange: (value: number | undefined) => void;
  inheritLabel?: string;
}) {
  const { t } = useTranslation();
  const preset = value === undefined ? "inherit" : AI_LIMIT_PRESETS.some((p) => p.tokens === value) ? String(value) : "custom";
  return (
    <span className="flex flex-wrap items-center gap-2">
      <select aria-label={t("Daily AI limit")} className="h-8 rounded-lg border border-[var(--border)] bg-[var(--background)] px-2 text-[12.5px]"
        value={preset}
        onChange={(e) => {
          const next = e.target.value;
          if (next === "inherit") onChange(undefined);
          else if (next === "custom") onChange(value && value > 0 ? value : 300_000);
          else onChange(Number(next));
        }}>
        {inheritLabel && <option value="inherit">{inheritLabel}</option>}
        {AI_LIMIT_PRESETS.map((p) => <option key={p.tokens} value={String(p.tokens)}>{t(p.label)}</option>)}
        <option value="custom">{t("A number of tokens")}</option>
      </select>
      {preset === "custom" && (
        // Committed when the field is left, not at every keystroke.
        <input key={value} type="number" min={1000} max={100_000_000} step={1000} aria-label={t("Tokens per day")}
          className="h-8 w-32 rounded-lg border border-[var(--border)] bg-[var(--background)] px-2 text-[12.5px]"
          defaultValue={value ?? ""}
          onBlur={(e) => {
            const next = Math.min(100_000_000, Math.max(1000, Number(e.target.value) || 1000));
            if (next !== value) onChange(next);
          }} />
      )}
    </span>
  );
}

/** The school default for students' daily AI (administrators; batch 3 §8). */
export function AiLimitSetting() {
  const { t } = useTranslation();
  const [value, setValue] = useState<number | undefined>(undefined);
  const [saved, setSaved] = useState<number | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    getSchoolAiLimit().then((v) => { setValue(v); setSaved(v); }).catch(() => setSaved(null));
  }, []);
  if (saved === null) return null;
  return (
    <section className="space-y-2 rounded-xl border border-[var(--border)] p-4" data-testid="ai-limit-setting">
      <h2 className="text-[13px] font-semibold">{t("Students' daily AI limit")}</h2>
      <p className="text-[12px] text-[var(--muted-foreground)]">
        {t("The default for every student. A student's own setting can change it. Course tasks can always be done and handed in.")}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <AiLimitPicker value={value ?? 0} onChange={(v) => setValue(v ?? 0)} />
        <button type="button" disabled={value === saved}
          className="rounded-lg bg-[var(--primary)] px-3 py-1.5 text-[12.5px] text-[var(--primary-foreground)] disabled:opacity-50"
          onClick={() => void setSchoolAiLimit(value ?? 0).then((v) => { setSaved(v); setError(""); })
            .catch((err) => setError(err instanceof Error ? t(err.message) : String(err)))}>
          {t("Save")}
        </button>
      </div>
      {error && <p role="alert" className="text-[12px] text-red-600">{error}</p>}
    </section>
  );
}
