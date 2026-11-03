"use client";

import Link from "next/link";
import { useTranslation } from "react-i18next";

import type { CatalogPreview as Preview } from "@/lib/catalog-api";
import { bookRoute } from "@/lib/learning-routes";
import type { TaskKind } from "@/lib/tasks-api";
import { KIND_LABEL } from "@/components/tasks/task-format";


const box = "rounded-xl border border-[var(--border)] p-3 text-[12.5px]";

/**
 * What an item holds (§5.1). The intro page never shows a practice set's
 * questions; ``full`` (the review page) shows them with their answers, the
 * path's goal, and opens a book's chapters.
 */
/** The practice-set editor's names for question types (keys already translated). */
const QUESTION_TYPE_LABELS: Record<string, string> = {
  choice: "Single choice",
  multi_choice: "Multiple choice",
  concept: "True or false",
  fill_in_blank: "Fill in the blank",
  short_answer: "Short answer",
  written: "Written answer",
  coding: "Coding",
};

export function CatalogPreview({ kind, itemId, preview, full = false }: { kind: string; itemId: string; preview: Preview; full?: boolean }) {
  const { t } = useTranslation();
  const empty = <p className="text-[var(--muted-foreground)]">{t("Nothing to preview yet.")}</p>;

  if (kind === "book") {
    const chapters = preview.chapters ?? [];
    return (
      <div className={box}>
        <h3 className="mb-2 font-semibold">{t("Chapters")}</h3>
        {chapters.length === 0 ? empty : (
          <ol className="list-decimal space-y-0.5 pl-5">
            {chapters.map((c) => <li key={c.id}>{c.title}</li>)}
          </ol>
        )}
        {full && (
          <Link href={bookRoute(`book:${itemId}`, null, "")} className="mt-2 inline-block text-[var(--primary)] hover:underline">
            {t("Read the chapters")}
          </Link>
        )}
      </div>
    );
  }
  if (kind === "knowledge_base") {
    const documents = preview.documents ?? [];
    return (
      <div className={box}>
        <h3 className="mb-2 font-semibold">{t("Documents")}</h3>
        {documents.length === 0 ? empty : (
          <ul className="list-disc space-y-0.5 break-all pl-5">
            {documents.map((d) => <li key={d}>{d}</li>)}
          </ul>
        )}
      </div>
    );
  }
  if (kind === "mastery_path") {
    const modules = preview.modules ?? [];
    return (
      <div className={`${box} space-y-2`}>
        <h3 className="font-semibold">{t("Modules and knowledge points")}</h3>
        {full && preview.goal && <p><span className="font-medium">{t("Goal")}: </span>{preview.goal}</p>}
        {modules.length === 0 ? empty : (
          <ol className="list-decimal space-y-1.5 pl-5">
            {modules.map((m, i) => (
              <li key={`${i}:${m.name}`}>
                <span className="font-medium">{m.name}</span>
                {m.points.length > 0 && <span className="block text-[var(--muted-foreground)]">{m.points.join(" · ")}</span>}
              </li>
            ))}
          </ol>
        )}
      </div>
    );
  }
  if (kind === "practice_set") {
    const types = Object.entries(preview.types ?? {});
    return (
      <div className={`${box} space-y-2`}>
        <h3 className="font-semibold">{t("Questions")}</h3>
        <p>
          {t("Questions: {{count}} · Total points: {{points}}", { count: preview.question_count ?? 0, points: preview.total_points ?? 0 })}
        </p>
        {types.length > 0 && (
          <p className="text-[var(--muted-foreground)]">{types.map(([type, n]) => `${QUESTION_TYPE_LABELS[type] ? t(QUESTION_TYPE_LABELS[type]) : type || "—"} × ${n}`).join(" · ")}</p>
        )}
        {full && (preview.questions ?? []).length > 0 && (
          <ol className="list-decimal space-y-3 pl-5">
            {(preview.questions ?? []).map((q, i) => (
              <li key={i} className="space-y-1">
                <p className="whitespace-pre-line">{q.question}</p>
                {Object.keys(q.options ?? {}).length > 0 && (
                  <ul className="pl-2 text-[var(--muted-foreground)]">
                    {Object.entries(q.options).map(([key, text]) => <li key={key}>{key}. {text}</li>)}
                  </ul>
                )}
                <p><span className="font-medium">{t("Answer")}: </span>{q.correct_answer}</p>
                {q.explanation && <p className="text-[var(--muted-foreground)]">{q.explanation}</p>}
                <p className="text-[11px] text-[var(--muted-foreground)]">{t("{{count}} points", { count: q.points })}</p>
              </li>
            ))}
          </ol>
        )}
      </div>
    );
  }
  if (kind === "course") {
    const items = preview.items ?? [];
    return (
      <div className={box}>
        <h3 className="mb-2 font-semibold">{t("Items")}</h3>
        {items.length === 0 ? empty : (
          <ol className="list-decimal space-y-0.5 pl-5">
            {items.map((item, i) => (
              <li key={i}>
                {item.title} <span className="text-[var(--muted-foreground)]">· {KIND_LABEL[item.kind as TaskKind] ? t(KIND_LABEL[item.kind as TaskKind]) : item.kind}</span>
              </li>
            ))}
          </ol>
        )}
      </div>
    );
  }
  return empty;
}

