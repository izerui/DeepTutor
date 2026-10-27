'use client'

import { Send, Users } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import type { MasteryTopic } from '@/lib/learning-api'
import type { TeacherTemplate } from '@/lib/teaching-api'

import { unpublishableReason } from './teaching'

const BUTTON =
  'inline-flex h-8 items-center gap-1 rounded-md border border-[var(--border)] px-2 font-medium text-[var(--muted-foreground)] transition hover:text-[var(--foreground)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]/40'

function TemplateStatus({ template }: { template: TeacherTemplate }) {
  const { t } = useTranslation()
  return (
    <>
      {template.current_revision > 0 && (
        <span className="text-[var(--muted-foreground)]">
          {t('Published v{{revision}}', { revision: template.current_revision })} ·{' '}
          {t('{{count}} students assigned', { count: template.assigned_count })}
        </span>
      )}
      {template.materials_unavailable.length > 0 && (
        <span className="text-amber-600">{t('Some course materials are unavailable')}</span>
      )}
    </>
  )
}

/** The footer of a teacher's own path card: its publishing state and controls. */
export function TeacherCardActions({
  topic,
  template,
  onPublish,
  onManage,
}: {
  topic: MasteryTopic
  /** Only a template this account owns, whose draft is this topic. */
  template?: TeacherTemplate
  onPublish: (
    topic: MasteryTopic,
    template: TeacherTemplate | undefined,
    trigger: HTMLButtonElement
  ) => void
  onManage: (template: TeacherTemplate, trigger: HTMLButtonElement) => void
}) {
  const { t } = useTranslation()
  const published = Boolean(template && template.current_revision > 0)

  return (
    <div className="flex flex-wrap items-center gap-2 border-t border-[var(--border)] px-4 py-2.5 text-[11px]">
      {template && <TemplateStatus template={template} />}
      <span className="ml-auto flex flex-wrap gap-1.5">
        <button
          type="button"
          onClick={event => onPublish(topic, template, event.currentTarget)}
          className={BUTTON}
        >
          <Send aria-hidden className="h-3 w-3" />
          {published ? t('Publish update') : t('Publish to students')}
        </button>
        {template && (
          <button
            type="button"
            onClick={event => onManage(template, event.currentTarget)}
            className={BUTTON}
          >
            <Users aria-hidden className="h-3 w-3" />
            {t('Manage assignments')}
          </button>
        )}
      </span>
    </div>
  )
}

/**
 * Teacher paths without a card here: another teacher's (an admin sees them),
 * a removed teacher's transferred path, or one whose draft is gone. They can
 * be managed but not published from this page, and the reason is said.
 */
export function UnlistedTeacherTemplates({
  templates,
  onManage,
}: {
  templates: TeacherTemplate[]
  onManage: (template: TeacherTemplate, trigger: HTMLButtonElement) => void
}) {
  const { t } = useTranslation()
  if (templates.length === 0) return null
  return (
    <section aria-labelledby="unlisted-teacher-paths" className="mt-10">
      <h2 id="unlisted-teacher-paths" className="text-sm font-semibold">
        {t('Other teacher paths')}
      </h2>
      <ul className="mt-3 divide-y divide-[var(--border)] rounded-xl border border-[var(--border)] bg-[var(--card)]">
        {templates.map(template => {
          const reason = unpublishableReason(template)
          return (
            <li
              key={template.id}
              className="flex flex-col gap-2 px-4 py-3 text-xs sm:flex-row sm:items-center"
            >
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline gap-x-2 text-sm">
                  <span className="truncate font-medium">{template.title}</span>
                  {!template.is_owner && template.owner_name && (
                    <span className="text-xs text-[var(--muted-foreground)]">
                      {t('by {{name}}', { name: template.owner_name })}
                    </span>
                  )}
                </div>
                <div className="mt-1 flex flex-wrap gap-x-2 text-[11px]">
                  <TemplateStatus template={template} />
                </div>
                <p className="mt-1 text-[var(--muted-foreground)]">
                  {t(reason.key, reason.values)}
                </p>
              </div>
              <button
                type="button"
                onClick={event => onManage(template, event.currentTarget)}
                className={`${BUTTON} self-start sm:self-center`}
              >
                <Users aria-hidden className="h-3 w-3" />
                {t('Manage assignments')}
              </button>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
