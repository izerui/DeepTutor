"use client";

import { ArrowLeft, Lock } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import BlockRenderer from "@/app/(workspace)/learning/books/components/blocks/BlockRenderer";
import { PAGE_TITLE_CLASS } from "@/components/layout/FeaturePage";
import type { Block } from "@/lib/book-types";
import { getBookTrial, type BookTrial as Trial } from "@/lib/catalog-api";

import { catalogError, itemHref } from "./catalog-format";

type TrialBlock = Trial["pages"][number]["blocks"][number];

/** The trial keeps only what a reader shows; the renderer's other fields get their empty values. */
function asBlock(block: TrialBlock): Block {
  return {
    params: {}, source_anchors: [], metadata: {}, error: "", created_at: 0, updated_at: 0,
    ...block,
  } as Block;
}

/**
 * Spec 2026-11-11 §4: a listed book's first chapter, read before joining
 * (``/market/try?id=…``). Read only: no reading progress, bookmarks, answers
 * or the book's AI — the blocks get no handlers. Other chapters: "Join to read".
 */
export default function BookTrial() {
  const { t } = useTranslation();
  const id = useSearchParams()?.get("id") ?? "";
  const [trial, setTrial] = useState<Trial | null>(null);
  const [error, setError] = useState("");
  const [locked, setLocked] = useState("");

  useEffect(() => {
    if (!id) return;
    let alive = true;
    getBookTrial(id)
      .then((next) => alive && setTrial(next))
      .catch((err) => alive && setError(catalogError(err, t)));
    return () => {
      alive = false;
    };
  }, [id, t]);

  const intro = itemHref("book", id);
  const back = (
    <Link href={intro} className="mb-4 inline-flex items-center gap-1.5 text-[12.5px] text-[var(--muted-foreground)] hover:text-[var(--foreground)]">
      <ArrowLeft size={14} />
      {t("Back to the introduction")}
    </Link>
  );
  if (!id) return <p className="text-[13px] text-[var(--muted-foreground)]">{t("Not found.")}</p>;
  if (!trial) {
    return (
      <div>
        {back}
        {error ? <p role="alert" className="text-[13px] text-red-600">{error}</p> : <div aria-busy="true" className="h-40 animate-pulse rounded-xl bg-[var(--muted)]" />}
      </div>
    );
  }

  return (
    <article className="space-y-5" data-testid="book-trial">
      {back}
      <header className="space-y-1">
        <p className="text-[12px] text-[var(--muted-foreground)]">{t("Try reading")}</p>
        <h1 className={PAGE_TITLE_CLASS}>{trial.book.title}</h1>
        <p className="text-[12.5px] text-[var(--muted-foreground)]">
          {t("You are reading the first chapter. Join to read the whole book.")}
        </p>
      </header>

      <nav aria-label={t("Chapters")} className="rounded-xl border border-[var(--border)] p-3 text-[12.5px]">
        <ol className="list-decimal space-y-0.5 pl-5">
          {trial.chapters.map((chapter) => (
            <li key={chapter.id}>
              {chapter.trial ? (
                <span className="font-medium" aria-current="true">{chapter.title}</span>
              ) : (
                <button type="button" className="inline-flex items-center gap-1 text-left text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
                  onClick={() => setLocked(chapter.id)}>
                  <Lock size={11} aria-hidden />
                  {chapter.title}
                </button>
              )}
              {locked === chapter.id && (
                <span role="status" className="ml-2 text-[var(--muted-foreground)]" data-testid="join-to-read">
                  {t("Join to read")}
                  {" · "}
                  <Link href={intro} className="text-[var(--primary)] hover:underline">{t("Back to the introduction")}</Link>
                </span>
              )}
            </li>
          ))}
        </ol>
      </nav>

      <section aria-label={trial.chapter.title} className="space-y-8">
        <h2 className="text-[17px] font-semibold">{trial.chapter.title}</h2>
        {trial.pages.map((page) => (
          <div key={page.id} className="space-y-4" data-testid="trial-page">
            {page.title && page.title !== trial.chapter.title && <h3 className="text-[15px] font-semibold">{page.title}</h3>}
            {page.blocks.map((block) => (
              <BlockRenderer
                key={block.id}
                block={asBlock(block)}
                mediaBookId={trial.book.id}
                currentPageId={page.id}
                bookLanguage={trial.book.language || undefined}
              />
            ))}
          </div>
        ))}
      </section>
    </article>
  );
}
