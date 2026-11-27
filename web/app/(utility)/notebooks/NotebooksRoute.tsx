'use client'

import { Suspense, useCallback, useEffect, useState } from 'react'
import { useParams, useSearchParams } from 'next/navigation'
import { Loader2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import NotebookConsole, { type NotebookCourseScope } from '@/components/notebook/NotebookConsole'
import { listCourses } from '@/lib/courses-api'

// `/notebooks` is the Notebooks console. The Question Bank — a separate
// feature that happens to share the word in its API path — lives at
// `/space/questions`.

function NotebookRoute() {
  const { t } = useTranslation()
  const routeParams = useParams<{ notebookId?: string }>()
  const searchParams = useSearchParams()
  const requested = routeParams.notebookId?.trim() || null
  // A course scope arrives as `/notebooks?course=<id>`, from the course page or
  // a Course Study hand-off. Resolved here rather than in the console so the
  // console stays a pure view over whatever list it is handed.
  const courseId = searchParams.get('course')?.trim() ?? ''
  const workspaceId = searchParams.get('dt_workspace') ?? searchParams.get('workspace') ?? ''
  const scopeKey = `${workspaceId}:${courseId}`
  const [resolvedScope, setResolvedScope] = useState<string | null>(null)
  const [scopeError, setScopeError] = useState<{ key: string; message: string } | null>(null)
  const [courseScope, setCourseScope] = useState<NotebookCourseScope | null>(null)

  // Bumped after the console attaches something, so the scope re-reads and the
  // notebook just created stops looking like it is outside the course.
  const [scopeVersion, setScopeVersion] = useState(0)
  const handleScopeChanged = useCallback(() => setScopeVersion(version => version + 1), [])

  useEffect(() => {
    if (!courseId) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setCourseScope(null)
      return
    }
    let cancelled = false
    setScopeError(null)
    void listCourses()
      .then(courses => {
        if (cancelled) return
        const course = courses.find(item => item.id === courseId)
        setCourseScope({
          id: courseId,
          name: course?.name ?? '',
          // A course that references no notebook scopes the list to nothing,
          // which is the honest answer — not "here is everything instead".
          notebookIds: (course?.resources ?? [])
            .filter(resource => resource.kind === 'notebook')
            .map(resource => resource.ref_id),
        })
        setResolvedScope(scopeKey)
      })
      .catch(error => {
        if (!cancelled)
          setScopeError({
            key: scopeKey,
            message: error instanceof Error ? error.message : String(error),
          })
      })
    return () => {
      cancelled = true
    }
  }, [courseId, scopeKey, scopeVersion])

  if (courseId && scopeError?.key === scopeKey) {
    return (
      <div role="alert" className="flex h-full flex-col items-center justify-center gap-3 p-6">
        <p>{scopeError.message}</p>
        <button type="button" className="dt-btn-primary px-3 py-1.5" onClick={handleScopeChanged}>
          {t('Retry')}
        </button>
      </div>
    )
  }
  if (courseId && resolvedScope !== scopeKey) {
    return (
      <div className="flex h-full items-center justify-center" aria-busy="true">
        <Loader2 className="h-5 w-5 animate-spin text-[var(--muted-foreground)]" />
      </div>
    )
  }

  return (
    <NotebookConsole
      key={scopeKey}
      initialNotebookId={requested}
      initialRecordId={searchParams.get('record')?.trim() || null}
      courseScope={courseId ? courseScope : null}
      onScopeChanged={handleScopeChanged}
    />
  )
}

export default function NotebookPage() {
  return (
    <Suspense
      fallback={
        <div className="flex h-full items-center justify-center">
          <Loader2 className="h-5 w-5 animate-spin text-[var(--muted-foreground)]" />
        </div>
      }
    >
      <NotebookRoute />
    </Suspense>
  )
}
