"use client";

import { BookPlus, Check, Loader2, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { bookApi } from "@/lib/book-api";
import type { Book, Page } from "@/lib/book-types";
import { bookRoute } from "@/lib/learning-routes";
import type { VideoLessonResult } from "@/lib/video-lesson-types";

/** The id a book is edited by: a shared book is addressed by its registry ref. */
function bookTarget(book: Book): string {
  return book.source === "shared" && book.ref ? book.ref : book.id;
}

function errorText(error: unknown, fallback: string): string {
  if (!(error instanceof Error) || !error.message) return fallback;
  // BookApiError reads "book api <path> → <status>: <server detail>".
  const detail = /→ \d+: ([\s\S]+)$/.exec(error.message);
  return detail ? detail[1] : error.message;
}

/**
 * Spec 2026-11-20 §6: put the lesson into a chapter of a book the reader can
 * edit. The server copies the page into the book, so the book's readers —
 * learners who joined it, a course's learners — can play it too.
 */
export default function AddVideoLessonToBook({
  result,
}: {
  result: VideoLessonResult;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [books, setBooks] = useState<Book[] | null>(null);
  const [bookId, setBookId] = useState("");
  const [pages, setPages] = useState<Page[] | null>(null);
  const [pageId, setPageId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [added, setAdded] = useState<{ book: Book; pageId: string } | null>(null);

  const openPicker = () => {
    setOpen(true);
    if (books !== null) return;
    bookApi
      .list()
      .then(({ books: all }) => {
        setBooks(all.filter((book) => book.can_edit !== false && book.page_count > 0));
      })
      .catch((reason: unknown) => {
        setBooks([]);
        setError(errorText(reason, t("Could not load your books.")));
      });
  };

  const selectedBook = books?.find((book) => book.id === bookId) ?? null;

  const chooseBook = (nextId: string) => {
    setBookId(nextId);
    setPages(null);
    setPageId("");
    setError("");
    const book = books?.find((item) => item.id === nextId);
    if (!book) return;
    bookApi
      .get(bookTarget(book), { includeBlocks: false })
      .then((detail) => {
        setPages(detail.pages);
        setPageId(detail.pages[0]?.id ?? "");
      })
      .catch((reason: unknown) => {
        setPages([]);
        setError(errorText(reason, t("Could not load the chapters.")));
      });
  };

  const submit = async () => {
    if (!selectedBook || !pageId) return;
    setBusy(true);
    setError("");
    try {
      await bookApi.insertVideoLesson({
        book_id: bookTarget(selectedBook),
        page_id: pageId,
        output_url: result.page.url,
        title: result.title,
        summary: result.response,
        duration_seconds: result.durationSeconds,
        expected_revision: selectedBook.revision,
      });
      setAdded({ book: selectedBook, pageId });
      setOpen(false);
    } catch (reason) {
      setError(errorText(reason, t("Could not add the video lesson to the book.")));
    } finally {
      setBusy(false);
    }
  };

  if (added) {
    return (
      <div className="flex flex-wrap items-center gap-2 text-[11px] text-[var(--muted-foreground)]" role="status">
        <Check size={12} className="text-[var(--primary)]" />
        <span>{t("Added to “{{book}}”", { book: added.book.title || added.book.id })}</span>
        <Link
          href={bookRoute(bookTarget(added.book), added.pageId)}
          className="font-medium text-[var(--primary)] hover:underline"
        >
          {t("Open the chapter")}
        </Link>
      </div>
    );
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={openPicker}
        className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)] hover:text-[var(--foreground)]"
      >
        <BookPlus size={12} />
        {t("Add to book")}
      </button>
    );
  }

  const selectClass =
    "w-full rounded-md border border-[var(--border)] bg-[var(--background)] px-2 py-1 text-[12px] text-[var(--foreground)]";

  return (
    <div
      className="space-y-2 rounded-xl border border-[var(--border)] bg-[var(--background)] p-3"
      data-testid="add-video-lesson-to-book"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-[12px] font-medium text-[var(--foreground)]">
          {t("Add to book")}
        </span>
        <button
          type="button"
          onClick={() => setOpen(false)}
          aria-label={t("Close")}
          className="rounded p-1 text-[var(--muted-foreground)] hover:bg-[var(--muted)]"
        >
          <X size={12} />
        </button>
      </div>
      <p className="text-[11px] leading-snug text-[var(--muted-foreground)]">
        {t("Everyone who can read the book can play it.")}
      </p>
      {books === null ? (
        <Loader2 size={14} className="animate-spin text-[var(--muted-foreground)]" />
      ) : books.length === 0 ? (
        <p className="text-[11px] text-[var(--muted-foreground)]">
          {t("You have no book with chapters to add it to.")}
        </p>
      ) : (
        <>
          <label className="block space-y-1 text-[11px] text-[var(--muted-foreground)]">
            <span>{t("Book")}</span>
            <select
              className={selectClass}
              value={bookId}
              onChange={(event) => chooseBook(event.target.value)}
            >
              <option value="">{t("Choose a book")}</option>
              {books.map((book) => (
                <option key={book.id} value={book.id}>
                  {book.title || book.id}
                </option>
              ))}
            </select>
          </label>
          {selectedBook ? (
            <label className="block space-y-1 text-[11px] text-[var(--muted-foreground)]">
              <span>{t("Chapter")}</span>
              {pages === null ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <select
                  className={selectClass}
                  value={pageId}
                  onChange={(event) => setPageId(event.target.value)}
                >
                  {pages.map((page) => (
                    <option key={page.id} value={page.id}>
                      {page.title || page.id}
                    </option>
                  ))}
                </select>
              )}
            </label>
          ) : null}
          <button
            type="button"
            onClick={() => void submit()}
            disabled={!selectedBook || !pageId || busy}
            className="inline-flex items-center gap-1 rounded-md bg-[var(--primary)] px-3 py-1 text-[12px] font-medium text-[var(--primary-foreground)] disabled:opacity-50"
          >
            {busy ? <Loader2 size={12} className="animate-spin" /> : <BookPlus size={12} />}
            {t("Add")}
          </button>
        </>
      )}
      {error ? (
        <p className="text-[11px] text-rose-600" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
