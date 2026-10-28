// B3-P15 item 2: a learner whose learning_policy has no `books` surface must
// not be offered the "Course books" link (the library answers 403 there).
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import type { MasteryTopic } from '@/lib/learning-api'

const auth = vi.hoisted(() => ({
  state: { loading: false, learningPolicy: null as { allowedSurfaces: string[] } | null },
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      key.replace(/{{(\w+)}}/g, (_match, name: string) => String(values?.[name] ?? '')),
  }),
}))
vi.mock('@/hooks/useAuthStatus', () => ({ useAuthStatus: () => auth.state }))

import { TeacherPathBanner } from '@/components/space/learning/TeacherPathBanner'

function teacherTopic(): MasteryTopic {
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

beforeEach(() => {
  auth.state = { loading: false, learningPolicy: null }
})
afterEach(() => cleanup())

it('hides the Course books link when the learning policy excludes books', () => {
  auth.state = { loading: false, learningPolicy: { allowedSurfaces: ['chat', 'reading'] } }
  render(<TeacherPathBanner topic={teacherTopic()} />)
  expect(screen.queryByText(/Course books/)).toBeNull()
  expect(screen.queryByRole('link', { name: 'Waves book' })).toBeNull()
  // The provenance line is unaffected.
  expect(screen.getByText('From tina')).toBeTruthy()
})

it('renders nothing in compact mode when the only content was the hidden books', () => {
  auth.state = { loading: false, learningPolicy: { allowedSurfaces: ['chat'] } }
  const { container } = render(<TeacherPathBanner compact topic={teacherTopic()} />)
  expect(container).toBeEmptyDOMElement()
})

it('keeps the link for a learner whose policy grants books', () => {
  auth.state = { loading: false, learningPolicy: { allowedSurfaces: ['chat', 'books'] } }
  render(<TeacherPathBanner topic={teacherTopic()} />)
  expect(screen.getByRole('link', { name: 'Waves book' }).getAttribute('href')).toBe(
    '/learning/books/book%3Abk_waves?mastery_template=tpl1'
  )
})

it('keeps the link for accounts without a learning policy', () => {
  render(<TeacherPathBanner topic={teacherTopic()} />)
  expect(screen.getByRole('link', { name: 'Waves book' })).toBeTruthy()
})
