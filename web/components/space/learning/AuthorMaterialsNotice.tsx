'use client'

import { useEffect, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import type { MasteryTopic } from '@/lib/learning-api'
import { listAuthorTemplates, type AuthorTemplate } from '@/lib/teaching-api'

import { templateForTopic } from './teaching'

/**
 * On a teacher's own draft: which published materials students can no longer
 * read (an admin made them private or deleted them, spec 7.4).
 */
export function AuthorMaterialsNotice({
  topic,
  revision = 0,
}: {
  topic: Pick<MasteryTopic, 'path_id' | 'content_workspace_id'>
  /** Re-checked when the path changes. */
  revision?: number
}) {
  const { t } = useTranslation()
  const pathId = topic.path_id
  const workspaceId = topic.content_workspace_id ?? ''
  const [found, setFound] = useState<{ key: string; template?: AuthorTemplate }>({
    key: '',
  })
  const key = `${pathId}\u0000${workspaceId}`

  useEffect(() => {
    let alive = true
    listAuthorTemplates()
      .then(templates => {
        if (!alive) return
        const template = templateForTopic(
          templates.filter(row => row.is_owner),
          { path_id: pathId, content_workspace_id: workspaceId }
        )
        setFound({ key, template })
      })
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [key, pathId, workspaceId, revision])

  // A previous path's answer is never shown on this one.
  const template = found.key === key ? found.template : undefined
  if (!template || template.materials_unavailable.length === 0) return null
  return (
    <div
      role="alert"
      className="mt-3 flex items-start gap-3 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2.5 text-sm"
    >
      <AlertTriangle aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
      <div className="min-w-0">
        <div className="font-medium">{t('Some course materials are unavailable')}</div>
        <p className="mt-0.5 break-words text-xs text-[var(--muted-foreground)]">
          {template.materials_unavailable.join(' · ')}
        </p>
        <p className="mt-0.5 text-xs text-[var(--muted-foreground)]">
          {t('Learners cannot continue this path until you replace them and publish an update.')}
        </p>
      </div>
    </div>
  )
}
