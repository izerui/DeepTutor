"use client";

import { Check } from "lucide-react";
import { useTranslation } from "react-i18next";

import { CREATE_KINDS, CREATION_KIND_ORDER, type CreationKind } from "@/lib/creations";

/**
 * Create mode picker, rendered inside the composer's shared resource panel.
 *
 * develop's composer has no upstream ComposerTray; this is the panel-row
 * equivalent of upstream's `CreateMenu`: picking Skill or Persona turns every
 * following turn into authoring that artifact, and picking the kind already
 * chosen clears it again.
 */
export default function CreateKindSelector({
  value,
  onChange,
}: {
  value: string | null;
  onChange: (value: CreationKind | null) => void;
}) {
  const { t } = useTranslation();
  const chosen = value && value in CREATE_KINDS ? (value as CreationKind) : null;
  return (
    <div className="w-full py-1">
      {CREATION_KIND_ORDER.map((kind) => {
        const def = CREATE_KINDS[kind];
        const Icon = def.icon;
        const selected = kind === chosen;
        return (
          <button
            key={kind}
            type="button"
            aria-pressed={selected}
            onClick={() => onChange(selected ? null : kind)}
            className={`flex w-full items-center gap-2.5 px-3 py-2.5 text-left transition-colors active:bg-[var(--muted)]/70 ${
              selected ? "bg-[var(--primary)]/[0.06]" : "hover:bg-[var(--muted)]/45"
            }`}
          >
            <Icon
              size={15}
              strokeWidth={1.7}
              className={`shrink-0 ${
                selected ? "text-[var(--primary)]" : "text-[var(--muted-foreground)]"
              }`}
            />
            <div className="min-w-0 flex-1">
              <div className="truncate text-[12.5px] font-medium leading-snug text-[var(--foreground)]">
                {t(def.labelKey)}
              </div>
              <div className="truncate text-[11px] leading-snug text-[var(--muted-foreground)]">
                {t(def.hintKey)}
              </div>
            </div>
            {selected ? (
              <Check size={14} strokeWidth={2} className="shrink-0 text-[var(--primary)]" />
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
