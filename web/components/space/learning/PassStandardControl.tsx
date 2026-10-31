"use client";

import { Loader2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useAuthStatus } from "@/hooks/useAuthStatus";
import { updateMasteryPassStandard, type MasteryTopic, type PassStandard } from "@/lib/learning-api";

const STANDARDS: { value: PassStandard; label: string; hint: string }[] = [
  {
    value: "relaxed",
    label: "Relaxed",
    hint: "Two right answers pass; one mistake rarely sets you back.",
  },
  {
    value: "standard",
    label: "Standard",
    hint: "Two right answers in a row pass; after a mistake, three more.",
  },
  {
    value: "strict",
    label: "Strict",
    hint: "Three right answers in a row pass; after a mistake, five more.",
  },
];

/**
 * How strict the scored mastery check is on this path. The learner picks it on
 * their own path; on a teacher's path it is the teacher's and read-only.
 * Explanation-type objectives are judged by the tutor and are not affected.
 */
export function PassStandardControl({
  topic,
  locked,
  onSaved,
}: {
  topic: MasteryTopic;
  locked: boolean;
  onSaved: (next: MasteryTopic) => void;
}) {
  const { t } = useTranslation();
  const { isTeacher, isAdmin } = useAuthStatus();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const current = topic.pass_standard ?? "strict";
  const selected = STANDARDS.find((item) => item.value === current) ?? STANDARDS[2];

  const choose = async (value: PassStandard) => {
    if (value === current || busy) return;
    setBusy(true);
    setError("");
    try {
      onSaved(await updateMasteryPassStandard(topic.path_id, value));
    } catch {
      setError(t("Could not save the pass standard. Try again."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-3 flex flex-col gap-1.5 text-[12px]">
      <div className="flex flex-wrap items-center gap-2">
        <span id="pass-standard-label" className="font-medium text-[var(--foreground)]">
          {t("Pass standard")}
        </span>
        {locked ? (
          <span className="rounded-md bg-[var(--muted)] px-2 py-0.5 font-medium text-[var(--foreground)]">
            {t(selected.label)}
            <span className="ml-1.5 font-normal text-[var(--muted-foreground)]">· {t("Set by your teacher")}</span>
          </span>
        ) : (
          <div
            role="radiogroup"
            aria-labelledby="pass-standard-label"
            className="inline-flex rounded-lg border border-[var(--border)] bg-[var(--card)] p-0.5"
          >
            {STANDARDS.map((item) => (
              <button
                key={item.value}
                type="button"
                role="radio"
                aria-checked={item.value === current}
                disabled={busy}
                onClick={() => void choose(item.value)}
                className={`h-7 rounded-md px-2.5 font-medium transition focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]/40 disabled:opacity-60 ${
                  item.value === current
                    ? "bg-[var(--primary)] text-[var(--primary-foreground)]"
                    : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
                }`}
              >
                {t(item.label)}
              </button>
            ))}
          </div>
        )}
        {busy && <Loader2 aria-hidden="true" className="h-3.5 w-3.5 animate-spin text-[var(--muted-foreground)]" />}
      </div>
      <p className="text-[11.5px] text-[var(--muted-foreground)]">
        {t(selected.hint)} {t("Explanation questions are judged by the tutor either way.")}
        {!locked && (isTeacher || isAdmin) && (
          <> {t("If you published this path, students get the new standard with your next update.")}</>
        )}
      </p>
      {error && (
        <p role="alert" className="text-[11.5px] text-red-600 dark:text-red-400">
          {error}
        </p>
      )}
    </div>
  );
}
