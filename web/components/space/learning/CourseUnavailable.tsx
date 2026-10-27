'use client'

import Link from 'next/link'
import { GraduationCap } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { MASTERY_HOME } from '@/lib/learning-routes'
import { COURSE_UNAVAILABLE_KEY } from '@/lib/teacher-path-messages'
import { scopedUrl } from '@/lib/workspace-scope'

/**
 * A teacher path the learner can no longer open: the teacher removed their
 * access or archived it. Shown instead of a page that would 403 on every read.
 */
export function CourseUnavailable() {
  const { t } = useTranslation()
  return (
    <div
      role="alert"
      className="mastery-shell flex h-full flex-col items-center justify-center px-6 text-center"
    >
      <GraduationCap
        aria-hidden="true"
        className="h-10 w-10 text-[var(--muted-foreground)] opacity-40"
      />
      <h1 className="mt-4 text-lg font-semibold">{t('This course is not available')}</h1>
      <p className="mt-2 max-w-md text-sm text-[var(--muted-foreground)]">
        {t(COURSE_UNAVAILABLE_KEY)}
      </p>
      <Link
        href={scopedUrl(MASTERY_HOME)}
        className="mt-5 rounded text-sm font-medium text-[var(--primary)] hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]"
      >
        {t('Back to topics')}
      </Link>
    </div>
  )
}
