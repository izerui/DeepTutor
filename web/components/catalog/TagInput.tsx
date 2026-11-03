"use client";

import { X } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

export const MAX_TAGS = 8;
export const MAX_TAG_LENGTH = 20;

/** Trimmed, one per case-folded spelling (the first kept), at most :data:`MAX_TAGS` (§3). */
export function addTag(tags: string[], raw: string): string[] {
  const tag = raw.split(/\s+/).filter(Boolean).join(" ");
  if (!tag || tag.length > MAX_TAG_LENGTH || tags.length >= MAX_TAGS) return tags;
  if (tags.some((existing) => existing.toLocaleLowerCase() === tag.toLocaleLowerCase())) return tags;
  return [...tags, tag];
}

export function TagInput({
  value,
  onChange,
  disabled = false,
}: {
  value: string[];
  onChange: (next: string[]) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState("");
  const full = value.length >= MAX_TAGS;
  const commit = () => {
    const next = addTag(value, draft);
    if (next !== value) onChange(next);
    if (next !== value || !draft.trim()) setDraft("");
  };
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-1.5">
        {value.map((tag) => (
          <span key={tag} className="inline-flex items-center gap-1 rounded-full bg-[var(--muted)] px-2 py-0.5 text-[12px]">
            {tag}
            <button type="button" disabled={disabled} aria-label={t("Remove tag {{tag}}", { tag })}
              className="rounded-full text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
              onClick={() => onChange(value.filter((v) => v !== tag))}>
              <X size={11} />
            </button>
          </span>
        ))}
        <input
          className="h-8 min-w-[10rem] flex-1 rounded-lg border border-[var(--border)] bg-[var(--background)] px-2 text-[12.5px] outline-none focus:border-[var(--ring)]"
          value={draft}
          maxLength={MAX_TAG_LENGTH}
          disabled={disabled || full}
          aria-label={t("Add a tag")}
          placeholder={full ? t("At most 8 tags") : t("Type a tag and press Enter")}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              commit();
            } else if (e.key === "Backspace" && !draft && value.length) {
              onChange(value.slice(0, -1));
            }
          }}
        />
      </div>
      <p className="text-[11px] text-[var(--muted-foreground)]">{t("Up to 8 tags, each at most 20 characters.")}</p>
    </div>
  );
}
