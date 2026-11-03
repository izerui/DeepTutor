"use client";

import { Sparkles } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { similarQuestions } from "@/lib/workbench-api";

/**
 * A student's remedial practice from their mistakes (spec 2026-11-03 batch 3 §3.2):
 * AI writes new questions like the chosen ones into their own bank, then
 * practice starts. Theirs alone; uses their own AI.
 */
export function SimilarQuestionsButton({
  ids,
  disabled,
  onMade,
}: {
  ids: number[];
  disabled: boolean;
  onMade: (ids: number[]) => void;
}) {
  const { t, i18n } = useTranslation();
  const [count, setCount] = useState(5);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <span className="flex flex-wrap items-center gap-1.5 text-sm" data-testid="similar-questions">
      <select aria-label={t("How many new questions")} value={count} onChange={(e) => setCount(Number(e.target.value))}
        className="h-9 rounded-lg border border-[var(--border)] bg-[var(--background)] px-2">
        {[3, 5, 10].map((n) => <option key={n} value={n}>{n}</option>)}
      </select>
      <button
        type="button"
        disabled={disabled || busy || ids.length === 0}
        className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--border)] px-3 py-2 font-medium hover:bg-[var(--muted)] disabled:opacity-40"
        onClick={() => {
          setBusy(true);
          setError("");
          similarQuestions(ids, count, i18n.language?.startsWith("zh") ? "zh" : "en")
            .then((made) => onMade(made.entry_ids))
            .catch((err) => setError(err instanceof Error ? t(err.message) : String(err)))
            .finally(() => setBusy(false));
        }}
      >
        <Sparkles size={14} />
        {busy ? t("Generating…") : t("New questions like these")}
      </button>
      {error && <span role="alert" className="text-[12px] text-red-600">{error}</span>}
    </span>
  );
}
