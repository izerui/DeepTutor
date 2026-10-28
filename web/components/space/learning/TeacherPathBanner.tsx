'use client'

import { useState } from 'react'
import Link from 'next/link'
import { AlertTriangle, BookOpen, GraduationCap, Megaphone, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { useAuthStatus } from '@/hooks/useAuthStatus'
import { acknowledgeTeacherUpdate, type MasteryTopic } from '@/lib/learning-api'
import { bookRoute } from '@/lib/learning-routes'
import { withMasteryTemplate } from '@/lib/mastery-template-scope'
import {
  MATERIALS_UNAVAILABLE_DETAIL_KEY,
  MATERIALS_UNAVAILABLE_KEY,
} from '@/lib/teacher-path-messages'

/** The path's books, by their canonical `book:<id>` ref (never re-prefixed). */
function courseBooks(topic: MasteryTopic) {
  return topic.sources
    .filter(source => source.kind === 'book' && source.source_id?.trim())
    .map(source => ({ ref: source.source_id.trim(), label: source.label || source.source_id }))
}

/**
 * What a learner needs to know about a path their teacher set: whose it is,
 * that its outline is not theirs to edit, that it just changed, and when its
 * materials are gone (the tutor will not teach it from its own knowledge).
 */
export function TeacherPathBanner({
  topic,
  compact = false,
}: {
  topic: MasteryTopic
  /** In a study session: only the notices and books, not the provenance line. */
  compact?: boolean
}) {
  const { t } = useTranslation()
  const { loading: authLoading, learningPolicy } = useAuthStatus()
  // Keyed by the revision it announced: a later update shows again.
  const [hiddenRevision, setHiddenRevision] = useState<number | null>(null)
  const origin = topic.origin
  if (!origin || origin.kind !== 'teacher_template') return null
  const notice =
    origin.update_notice && origin.update_notice.to_revision !== hiddenRevision
      ? origin.update_notice
      : null
  // A learning policy without the `books` surface makes the library a 403;
  // the link is absent rather than offered and then rejected (B3-P15). Until
  // the status is known nothing is offered, so a learner never sees it flash.
  const booksAllowed =
    !authLoading && (!learningPolicy || learningPolicy.allowedSurfaces.includes('books'))
  const books = booksAllowed ? courseBooks(topic) : []
  const changes = notice
    ? [
        notice.added && t('{{count}} knowledge points added', { count: notice.added }),
        notice.removed && t('{{count}} knowledge points removed', { count: notice.removed }),
        notice.redefined &&
          t('{{count}} knowledge points to re-check', { count: notice.redefined }),
      ].filter(Boolean)
    : []

  const dismiss = () => {
    if (!notice) return
    const revision = notice.to_revision
    setHiddenRevision(revision)
    // A failed acknowledgement would bring the notice back on the next
    // visit; show it again now rather than pretend it was recorded.
    acknowledgeTeacherUpdate(topic.path_id).catch(() =>
      setHiddenRevision(current => (current === revision ? null : current))
    )
  }

  const hasContent = !compact || books.length > 0 || notice || origin.materials_available === false
  if (!hasContent) return null

  return (
    <div className={`flex flex-col gap-2 ${compact ? 'px-3 pt-2 sm:px-4' : ''}`}>
      {!compact && (
        <p className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[12px] text-[var(--muted-foreground)]">
          <GraduationCap aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
          <span>{t('From {{teacher}}', { teacher: origin.teacher_name || t('Teacher') })}</span>
          <span aria-hidden="true">·</span>
          <span>{t('The outline is set by your teacher')}</span>
        </p>
      )}
      {books.length > 0 && (
        // R9: the books this path cites open with the path's context, so a
        // book shared "by reference only" is readable from here.
        <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-[var(--muted-foreground)]">
          <BookOpen aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
          <span>{t('Course books')}:</span>
          {books.map(book => (
            <Link
              key={book.ref}
              href={withMasteryTemplate(bookRoute(book.ref, null, ''), origin.template_id)}
              className="rounded text-[var(--primary)] hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
            >
              {book.label}
            </Link>
          ))}
        </p>
      )}
      {notice && (
        <div
          role="status"
          className="flex items-start gap-3 rounded-lg border border-[var(--border)] bg-[var(--muted)]/50 px-3 py-2.5 text-sm"
        >
          <Megaphone aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-[var(--primary)]" />
          <div className="min-w-0 flex-1">
            <div className="font-medium">{t('Your teacher updated this course')}</div>
            {changes.length > 0 && (
              <p className="mt-0.5 text-xs text-[var(--muted-foreground)]">{changes.join(' · ')}</p>
            )}
          </div>
          <button
            type="button"
            onClick={dismiss}
            className="inline-flex min-h-7 shrink-0 items-center gap-1 rounded-md px-2 text-xs text-[var(--muted-foreground)] hover:bg-[var(--accent)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
          >
            <X aria-hidden="true" className="h-3.5 w-3.5" />
            {t('Got it')}
          </button>
        </div>
      )}
      {origin.materials_available === false && (
        <div
          role="alert"
          className="flex items-start gap-3 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2.5 text-sm"
        >
          <AlertTriangle aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
          <div>
            <div className="font-medium">{t(MATERIALS_UNAVAILABLE_KEY)}</div>
            <p className="mt-0.5 text-xs text-[var(--muted-foreground)]">
              {t(MATERIALS_UNAVAILABLE_DETAIL_KEY)}
            </p>
          </div>
        </div>
      )}
    </div>
  )
}
