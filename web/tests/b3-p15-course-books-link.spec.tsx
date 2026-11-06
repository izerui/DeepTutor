// B3-P15 item 2, spec 2026-11-06: every learner is offered the "Course books"
// link; learning restrictions no longer hide it.
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import type { MasteryTopic } from '@/lib/learning-api'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      key.replace(/{{(\w+)}}/g, (_match, name: string) => String(values?.[name] ?? '')),
  }),
}))

import { AuthorPathBanner } from '@/components/space/learning/AuthorPathBanner'

function authorTopic(): MasteryTopic {
  return {
    path_id: 'assigned_tpl1',
    name: 'Waves',
    metadata: {
      path_id: 'assigned_tpl1', goal: 'Goal', description: '', emoji: '', map_seed: 1,
      status: 'active', created_at: 1, updated_at: 1,
    },
    sources: [
      {
        id: 's1', kind: 'book', source_id: 'book:bk_waves', label: 'Waves book', excerpt: '',
        position: 0, available: true, metadata: {}, created_at: 1,
      },
    ],
    path_revision: 1,
    next: null,
    origin: {
      kind: 'teacher_template', template_id: 'tpl1', revision: 1, teacher_name: 'tina',
      update_notice: null, materials_available: true,
    },
  } as unknown as MasteryTopic
}

afterEach(() => cleanup())

it('links the course books for every learner', () => {
  render(<AuthorPathBanner topic={authorTopic()} />)
  expect(screen.getByRole('link', { name: 'Waves book' }).getAttribute('href')).toBe(
    '/learning/books/book%3Abk_waves?mastery_template=tpl1'
  )
  expect(screen.getByText('Published by tina')).toBeTruthy()
})
