"use client";

import { MessageSquare } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  deleteComment,
  myComments,
  writeComment,
  writtenComments,
  type CommentKind,
  type TeacherComment,
} from "@/lib/comments-api";

const errorText = (err: unknown, t: (key: string) => string) => (err instanceof Error ? t(err.message) : String(err));

/**
 * A teacher's one comment to one student on one piece of work (spec 2026-11-03 batch 2 §3).
 * ``subject`` is the practice set or the path template; ``target`` the work within it.
 */
export function CommentEditor({
  kind,
  subject,
  target,
  studentId,
}: {
  kind: CommentKind;
  subject: string;
  target: string;
  studentId: string;
}) {
  const { t } = useTranslation();
  const ref = `${subject}:${target}`;
  const [open, setOpen] = useState(false);
  const [current, setCurrent] = useState<TeacherComment | null | undefined>(undefined);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let alive = true;
    writtenComments(kind, subject, studentId)
      .then((rows) => {
        if (!alive) return;
        const mine = rows.find((row) => row.ref === ref) ?? null;
        setCurrent(mine);
        setText(mine?.text ?? "");
      })
      .catch(() => alive && setCurrent(null));
    return () => {
      alive = false;
    };
  }, [kind, subject, studentId, ref]);

  if (current === undefined) return null;
  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)}
        className="inline-flex items-center gap-1 text-[11.5px] text-[var(--primary)] hover:underline">
        <MessageSquare size={11} />
        {current ? t("Edit comment") : t("Comment")}
      </button>
    );
  }
  return (
    <div className="space-y-1.5" data-testid="comment-editor">
      <textarea aria-label={t("Comment to the student")} rows={2} maxLength={5000} value={text}
        onChange={(e) => { setText(e.target.value); setSaved(false); }}
        className="w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-2 py-1 text-[12.5px]" />
      <div className="flex items-center gap-2 text-[11.5px]">
        <button type="button" disabled={!text.trim()}
          className="rounded-md bg-[var(--primary)] px-2 py-1 font-medium text-[var(--primary-foreground)] disabled:opacity-50"
          onClick={() => void writeComment(kind, ref, studentId, text.trim())
            .then((next) => { setCurrent(next); setSaved(true); setError(""); })
            .catch((err) => setError(errorText(err, t)))}>
          {t("Save comment")}
        </button>
        {current && (
          <button type="button" className="text-red-600 hover:underline"
            onClick={() => void deleteComment(current.id)
              .then(() => { setCurrent(null); setText(""); setOpen(false); })
              .catch((err) => setError(errorText(err, t)))}>
            {t("Delete")}
          </button>
        )}
        <button type="button" className="text-[var(--muted-foreground)] hover:underline" onClick={() => setOpen(false)}>
          {t("Close")}
        </button>
        {saved && <span role="status" className="text-[var(--muted-foreground)]">{t("Saved.")}</span>}
      </div>
      {error && <p role="alert" className="text-[11.5px] text-red-600">{error}</p>}
    </div>
  );
}

/** A student's comments from teachers on one practice set or Mastery Path. */
export function MyComments({
  kind,
  subject,
  label,
}: {
  kind: CommentKind;
  subject: string;
  /** What a comment is about, from the part of its ref after the subject. */
  label?: (target: string) => string;
}) {
  const { t } = useTranslation();
  const [rows, setRows] = useState<TeacherComment[]>([]);

  useEffect(() => {
    let alive = true;
    myComments(kind, subject)
      .then((next) => alive && setRows(next))
      .catch(() => alive && setRows([]));
    return () => {
      alive = false;
    };
  }, [kind, subject]);

  if (!rows.length) return null;
  return (
    <section className="space-y-1.5 rounded-xl border border-[var(--border)] p-3" data-testid="my-comments">
      <h3 className="flex items-center gap-1.5 text-[12.5px] font-medium">
        <MessageSquare size={13} />
        {t("Teacher comments")}
      </h3>
      <ul className="space-y-1.5">
        {rows.map((row) => {
          const about = label?.(row.ref.slice(subject.length + 1));
          return (
            <li key={row.id} className="text-[12.5px]">
              {about && <span className="mr-1 text-[var(--muted-foreground)]">{about}:</span>}
              <span className="whitespace-pre-line text-[var(--foreground)]">{row.text}</span>
              <span className="ml-2 text-[11px] text-[var(--muted-foreground)]">
                {row.author || t("Former teacher")} · {new Date(row.updated_at * 1000).toLocaleString()}
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
