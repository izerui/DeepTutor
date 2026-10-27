'use client'

import { WorkspaceLabel } from '@/components/learning/LibraryWorkspace'
import { masteryTopicRoute } from '@/lib/learning-routes'

import type { ReactNode } from 'react'
import Link from 'next/link'
import { useTranslation } from 'react-i18next'
import { CircleCheck, Route } from 'lucide-react'

import type { MasteryTopic } from '@/lib/learning-api'

import { topicDisplayName } from './format'
import { LearningCardContent } from '@/components/learning/LearningCard'

export function TopicMapCard({
  topic,
  href,
  badge,
  footer,
}: {
  topic: MasteryTopic
  /** Where the card opens; the topic dashboard by default. */
  href?: string
  /** Shown beside the workspace label, e.g. the assigning teacher. */
  badge?: ReactNode
  /** Rendered below the link, outside it, so it may hold its own buttons. */
  footer?: ReactNode
}) {
  const { t } = useTranslation()
  const { map, metadata } = topic
  const total = map.counts.total
  const mastered = map.counts.mastered
  const progress = total ? mastered / total : 0
  const displayName = topicDisplayName(topic, t)

  // The card is a box rather than a link so a footer may carry buttons
  // (interactive content cannot nest inside a link).
  return (
    <div className="mastery-map-card group overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--card)]">
      <Link
        href={href ?? masteryTopicRoute(topic.path_id, topic.content_workspace_id)}
        aria-label={t('Open {{name}}, {{mastered}} of {{total}} knowledge points complete', {
          name: displayName,
          mastered,
          total,
        })}
        className={`block ${footer ? 'rounded-t-xl' : 'rounded-xl'} focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--ring)]`}
      >
        <div className="flex items-center gap-2 px-4 pt-3">
          <WorkspaceLabel row={topic} />
          {badge}
        </div>
        <div className="flex items-center gap-3 p-4">
          <LearningCardContent
            title={displayName}
            subtitle={metadata.description || metadata.goal}
            icon={map.complete ? <CircleCheck size={19} /> : <Route size={19} />}
            updatedAt={topic.updated_at}
            progress={progress}
          />
        </div>
        <div className="flex flex-wrap gap-x-3 gap-y-1 border-t border-[var(--border)] px-4 py-3 text-[11px] text-[var(--muted-foreground)]">
          <span>{t('{{count}} modules', { count: map.modules.length })}</span>
          <span>
            {mastered}/{total} {t('knowledge points')}
          </span>
          <span>{t('{{count}} sessions', { count: topic.session_count })}</span>
        </div>
      </Link>
      {footer}
    </div>
  )
}
