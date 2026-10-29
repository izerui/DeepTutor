"use client";

import { useTranslation } from "react-i18next";

import type { PracticeQuestion } from "@/lib/practice-sets-api";

/** One answer control per question type (choice keys are stored like "A" or "A,C"). */
export function QuestionAnswer({
  question,
  value,
  onChange,
  disabled,
}: {
  question: PracticeQuestion;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const name = `answer-${question.question_id}`;
  if (question.question_type === "choice" || question.question_type === "multi_choice") {
    const multi = question.question_type === "multi_choice";
    const chosen = new Set(value.split(",").filter(Boolean));
    return (
      <div className="space-y-1">
        {Object.entries(question.options).map(([key, text]) => (
          <label key={key} className="flex items-start gap-2 text-[13px]">
            <input
              type={multi ? "checkbox" : "radio"}
              name={name}
              disabled={disabled}
              checked={chosen.has(key)}
              onChange={() => {
                if (!multi) return onChange(key);
                const next = new Set(chosen);
                if (next.has(key)) next.delete(key);
                else next.add(key);
                onChange([...next].sort().join(","));
              }}
            />
            <span>
              <span className="font-medium">{key}.</span> {text}
            </span>
          </label>
        ))}
      </div>
    );
  }
  if (question.question_type === "concept") {
    return (
      <div className="flex gap-4 text-[13px]">
        {[["True", t("True")], ["False", t("False")]].map(([key, label]) => (
          <label key={key} className="flex items-center gap-1.5">
            <input type="radio" name={name} disabled={disabled} checked={value === key} onChange={() => onChange(key)} />
            {label}
          </label>
        ))}
      </div>
    );
  }
  const short = question.question_type === "fill_in_blank";
  return short ? (
    <input
      aria-label={t("Your answer")}
      disabled={disabled}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="h-9 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 text-[13px]"
    />
  ) : (
    <textarea
      aria-label={t("Your answer")}
      disabled={disabled}
      value={value}
      rows={question.question_type === "coding" ? 8 : 4}
      onChange={(e) => onChange(e.target.value)}
      className={`w-full rounded-lg border border-[var(--border)] bg-[var(--background)] p-3 text-[13px] ${
        question.question_type === "coding" ? "font-mono" : ""
      }`}
    />
  );
}
