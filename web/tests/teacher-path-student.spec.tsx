import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { StreamEvent } from '@/features/chat/model/protocol'
import type { MasteryTopic, ObjectiveReport } from '@/lib/learning-api'

const api = vi.hoisted(() => ({
  acknowledgeTeacherUpdate: vi.fn(),
  fetchObjectiveReport: vi.fn(),
}))
const apiFetch = vi.hoisted(() => vi.fn())

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      key.replace(/{{(\w+)}}/g, (_match, name: string) => String(values?.[name] ?? '')),
    i18n: { language: 'en' },
  }),
}))
vi.mock('@/lib/learning-api', async original => ({
  ...(await original<typeof import('@/lib/learning-api')>()),
  ...api,
}))
vi.mock('@/lib/api', async original => ({
  ...(await original<typeof import('@/lib/api')>()),
  apiFetch,
  apiUrl: (url: string) => url,
}))

import { MasterySourcePresentation } from '@/components/chat/home/MasterySourcePresentation'
import { MasteryTemplateContext } from '@/components/space/learning/mastery-template-context'
import { ModeSwitch } from '@/components/space/learning/ModeSwitch'
import { ModuleOutline } from '@/components/space/learning/ModuleOutline'
import { ObjectiveDetail } from '@/components/space/learning/ObjectiveDetail'
import { PathTitle } from '@/components/space/learning/PathTitle'
import { TeacherPathBanner } from '@/components/space/learning/TeacherPathBanner'
import { TopicAtlas } from '@/components/space/learning/TopicAtlas'
import { MasteryApiError, isCourseUnavailable } from '@/lib/learning-api'
import { teacherTurnErrorMessage } from '@/lib/teacher-path-messages'

function topic(overrides: Partial<MasteryTopic> = {}): MasteryTopic {
  return {
    path_id: 'own_path',
    name: 'Own path',
    metadata: {
      path_id: 'own_path',
      goal: 'Goal',
      description: '',
      emoji: '',
      map_seed: 1,
      status: 'active',
      created_at: 1,
      updated_at: 1,
    },
    sources: [],
    path_revision: 1,
    next: {
      action: 'learn',
      knowledge_point_id: 'kp1',
      knowledge_point_name: 'Wavelength',
      reason: '',
    },
    map: {
      name: 'Own path',
      counts: { mastered: 0, learning: 0, new: 1, total: 1 },
      due_reviews: 0,
      complete: false,
      modules: [
        {
          id: 'm1',
          name: 'Waves',
          objective: '',
          order: 0,
          mastered: 0,
          total: 1,
          knowledge_points: [
            {
              id: 'kp1',
              name: 'Wavelength',
              type: 'concept',
              prerequisite_ids: [],
              topic_source_ids: [],
              status: 'learning',
              mastery: 0.2,
              mastery_source: '',
              override_note: '',
            },
          ],
        },
      ],
    },
    reviews: [],
    learner_profile: null,
    session_count: 0,
    updated_at: 1,
    origin: null,
    ...overrides,
  } as MasteryTopic
}

function teacherTopic(overrides: Partial<MasteryTopic> = {}): MasteryTopic {
  return topic({
    path_id: 'assigned_tpl1',
    name: 'C15 Waves',
    origin: {
      kind: 'teacher_template',
      template_id: 'tpl1',
      revision: 2,
      teacher_name: 'tina',
      update_notice: null,
      materials_available: true,
    },
    ...overrides,
  })
}

beforeEach(() => {
  api.acknowledgeTeacherUpdate.mockReset().mockResolvedValue({ status: 'ok', acknowledged: true })
  apiFetch.mockReset()
  window.history.replaceState(null, '', '/')
})

describe('TopicAtlas for students', () => {
  it('groups teacher paths first, opens them straight into Study, and hides creation without permission', () => {
    render(
      <TopicAtlas
        topics={[topic(), teacherTopic()]}
        loading={false}
        error={null}
        onRetry={() => {}}
      />
    )
    const assigned = screen.getByRole('region', { name: 'Assigned by your teachers' })
    const link = within(assigned).getByRole('link', { name: /C15 Waves/ })
    expect(link.getAttribute('href')).toBe('/learning/mastery/assigned_tpl1/sessions')
    expect(within(assigned).getByText('From tina')).toBeInTheDocument()
    expect(within(assigned).getByText('Next: Wavelength')).toBeInTheDocument()
    const own = screen.getByRole('region', { name: 'Active learning topics' })
    expect(within(own).getByRole('heading', { name: 'My topics' })).toBeInTheDocument()
    expect(within(own).queryByText('C15 Waves')).toBeNull()
    expect(screen.queryByRole('button', { name: /New topic/ })).toBeNull()
  })

  it('keeps the create control and the single grid for personal-only libraries', () => {
    const onCreate = vi.fn()
    render(
      <TopicAtlas
        topics={[topic()]}
        loading={false}
        error={null}
        onRetry={() => {}}
        onCreate={onCreate}
      />
    )
    expect(screen.queryByRole('region', { name: 'Assigned by your teachers' })).toBeNull()
    expect(screen.queryByRole('heading', { name: 'My topics' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /New topic/ }))
    expect(onCreate).toHaveBeenCalledTimes(1)
  })

  it('offers no dead create control in an empty library without permission', () => {
    render(<TopicAtlas topics={[]} loading={false} error={null} onRetry={() => {}} />)
    expect(
      screen.getByText('Paths your teachers assign to you will appear here.')
    ).toBeInTheDocument()
    expect(screen.queryByRole('button')).toBeNull()
  })
})

describe('TeacherPathBanner', () => {
  it('shows provenance, the update notice with counts, and acknowledges it once', async () => {
    render(
      <TeacherPathBanner
        topic={teacherTopic({
          origin: {
            kind: 'teacher_template',
            template_id: 'tpl1',
            revision: 2,
            teacher_name: 'tina',
            update_notice: {
              from_revision: 1,
              to_revision: 2,
              added: 1,
              removed: 0,
              redefined: 2,
              applied_at: 1,
            },
            materials_available: true,
          },
        })}
      />
    )
    expect(screen.getByText('From tina')).toBeInTheDocument()
    expect(screen.getByText('The outline is set by your teacher')).toBeInTheDocument()
    const notice = screen.getByRole('status')
    expect(notice).toHaveTextContent('Your teacher updated this course')
    expect(notice).toHaveTextContent('1 knowledge points added · 2 knowledge points to re-check')
    expect(notice).not.toHaveTextContent('removed')
    fireEvent.click(within(notice).getByRole('button', { name: /Got it/ }))
    expect(screen.queryByRole('status')).toBeNull()
    expect(api.acknowledgeTeacherUpdate).toHaveBeenCalledWith('assigned_tpl1')
  })

  it('brings the notice back when the acknowledgement fails', async () => {
    api.acknowledgeTeacherUpdate.mockRejectedValue(new Error('offline'))
    render(
      <TeacherPathBanner
        compact
        topic={teacherTopic({
          origin: {
            kind: 'teacher_template',
            template_id: 'tpl1',
            revision: 2,
            teacher_name: 'tina',
            update_notice: {
              from_revision: 1,
              to_revision: 2,
              added: 0,
              removed: 1,
              redefined: 0,
              applied_at: 1,
            },
            materials_available: true,
          },
        })}
      />
    )
    fireEvent.click(screen.getByRole('button', { name: /Got it/ }))
    expect(await screen.findByRole('status')).toHaveTextContent('1 knowledge points removed')
  })

  it('links course books by canonical ref with the path context, and alerts on missing materials', () => {
    render(
      <TeacherPathBanner
        compact
        topic={teacherTopic({
          sources: [
            {
              id: 's1',
              kind: 'book',
              source_id: 'book:bk_waves',
              label: 'Waves book',
              excerpt: '',
              position: 0,
              available: true,
              metadata: {},
              created_at: 1,
            },
          ],
          origin: {
            kind: 'teacher_template',
            template_id: 'tpl1',
            revision: 2,
            teacher_name: 'tina',
            update_notice: null,
            materials_available: false,
          },
        })}
      />
    )
    // Encoded once (book%3A...), never re-prefixed or double-encoded.
    expect(screen.getByRole('link', { name: 'Waves book' }).getAttribute('href')).toBe(
      '/learning/books/book%3Abk_waves?mastery_template=tpl1'
    )
    expect(screen.queryByText('From tina')).toBeNull()
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Course materials are temporarily unavailable'
    )
  })

  it('renders nothing for a path the learner built', () => {
    const { container } = render(<TeacherPathBanner topic={topic()} />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('read-only outline controls', () => {
  it('offers only study and review when told so', () => {
    const onSelect = vi.fn()
    render(<ModeSwitch mode="study" onSelect={onSelect} modes={['study', 'review']} />)
    expect(screen.getAllByRole('button')).toHaveLength(2)
    expect(screen.queryByRole('button', { name: 'masteryMode.outline' })).toBeNull()
  })

  it('has no rename control without onRename', () => {
    render(<PathTitle displayName="C15 Waves" storedName="C15 Waves" />)
    expect(screen.getByRole('heading', { name: 'C15 Waves' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Rename' })).toBeNull()
  })

  it('marks a redefined objective and offers no manual mastery on a teacher path', async () => {
    api.fetchObjectiveReport.mockResolvedValue(report({ needs_reassessment: true }))
    const path = teacherTopic()
    path.map.modules[0].knowledge_points[0].needs_reassessment = true
    render(
      <ModuleOutline topic={path} revision={1} selectedId="kp1" zh={false} onSelect={() => {}} />
    )
    expect(screen.getAllByText('Needs re-assessment').length).toBeGreaterThan(0)
    await screen.findByText('Collapse')
    expect(screen.queryByText('I already know this — advance')).toBeNull()
  })

  it("keeps manual mastery for a learner's own path", async () => {
    api.fetchObjectiveReport.mockResolvedValue(report())
    render(
      <ModuleOutline
        topic={topic()}
        revision={1}
        selectedId="kp1"
        zh={false}
        onSelect={() => {}}
        onOverride={vi.fn()}
      />
    )
    expect(await screen.findByText('I already know this — advance')).toBeInTheDocument()
  })
})

function report(overrides: Partial<ObjectiveReport> = {}): ObjectiveReport {
  return {
    id: 'kp1',
    name: 'Wavelength',
    type: 'concept',
    module_name: 'Waves',
    status: 'learning',
    gate: 'quantitative',
    mastered: false,
    assessed_mastered: false,
    mastery_source: '',
    override_note: '',
    mastery: 0.2,
    threshold: 0.8,
    attempts: [],
    correct_count: 0,
    explanation: '',
    review: null,
    errors: [],
    ...overrides,
  }
}

describe('ObjectiveDetail voided attempts', () => {
  it('excludes voided answers from the total and marks them', () => {
    const attempt = (id: string, extra: object) => ({
      question_id: id,
      prompt: `Question ${id}`,
      answer: 'a',
      is_correct: true,
      error_type: '',
      at: 1,
      ...extra,
    })
    render(
      <ObjectiveDetail
        zh={false}
        report={report({
          correct_count: 1,
          attempts: [
            attempt('q1', { voided: true, void_reason: 'objective_redefined' }),
            attempt('q2', { voided: true, void_reason: 'objective_redefined' }),
            attempt('q3', { voided: true, void_reason: 'unsupported key' }),
            attempt('q4', {}),
          ],
        })}
      />
    )
    expect(screen.getByText('Attempts (1/1 correct)')).toBeInTheDocument()
    expect(screen.getAllByText('Previous objective')).toHaveLength(2)
    expect(screen.getAllByText('Not counted')).toHaveLength(1)
  })
})

describe("originals inside a teacher path's Study page", () => {
  const preview = '/api/knowledge-bases/kb%3Akb1/source-page?source_path=lesson.pdf&page=1'
  const sources: StreamEvent = {
    type: 'sources',
    source: 'mastery_path',
    stage: 'responding',
    content: '',
    timestamp: 1,
    metadata: {
      sources: [{ title: 'Lesson page 1', preview_url: preview, presentation: 'teaching' }],
    },
  }

  it("reads with mastery_template only inside the path's context", async () => {
    apiFetch.mockResolvedValue({
      ok: true,
      headers: new Headers(),
      arrayBuffer: async () => new ArrayBuffer(4),
    })
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:page')
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    const { unmount } = render(
      <MasteryTemplateContext.Provider value="tpl1">
        <MasterySourcePresentation events={[sources]} />
      </MasteryTemplateContext.Provider>
    )
    await screen.findByRole('img', { name: 'Lesson page 1' })
    expect(apiFetch).toHaveBeenCalledWith(`${preview}&mastery_template=tpl1`, expect.anything())
    unmount()
    apiFetch.mockClear()
    render(<MasterySourcePresentation events={[sources]} />)
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith(preview, expect.anything()))
  })

  it('shows the course as unavailable on a 403, not a broken viewer', async () => {
    apiFetch.mockResolvedValue({ ok: false, status: 403 })
    render(
      <MasteryTemplateContext.Provider value="tpl1">
        <MasterySourcePresentation events={[sources]} />
      </MasteryTemplateContext.Provider>
    )
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(
        'This course is no longer available to you.'
      )
    )
    expect(screen.queryByRole('img')).toBeNull()
  })
})

describe('teacher path refusals are localized by code', () => {
  it('maps turn error codes and HTTP status, never the English text', async () => {
    expect(teacherTurnErrorMessage({ error_code: 'teacher_path_unavailable' })?.key).toMatch(
      /^This course is no longer available/
    )
    expect(
      teacherTurnErrorMessage({
        error_code: 'teacher_materials_unavailable',
        materials: ['C15 Notes', 'Waves book'],
      })
    ).toEqual({
      key: 'Course materials are temporarily unavailable: {{materials}}. Ask your teacher to restore them. The tutor will not continue without them.',
      values: { materials: 'C15 Notes · Waves book' },
    })
    expect(teacherTurnErrorMessage({ error_code: 'internal_error' })).toBeNull()
    expect(isCourseUnavailable(new MasteryApiError('x', 403), 'assigned_tpl1')).toBe(true)
    expect(isCourseUnavailable(new MasteryApiError('x', 403), 'own_path')).toBe(false)
    expect(isCourseUnavailable(new MasteryApiError('x', 404), 'assigned_tpl1')).toBe(false)
    await act(async () => {})
  })
})

describe('a book opened from a teacher path keeps its context', () => {
  it("carries mastery_template between the book's pages, not to the library", async () => {
    const { bookRoute } = await import('@/lib/learning-routes')
    const { bookErrorMessage } = await import('@/lib/book-errors')
    const { BookApiError } = await import('@/lib/book-api')
    const t = (key: string) => key
    expect(bookErrorMessage(new BookApiError('x', 403), t)).toBe('x')
    window.history.replaceState(null, '', '/learning/books/book%3Abk1?mastery_template=tpl1')
    expect(bookRoute('book:bk1', 'pg2', '')).toBe(
      '/learning/books/book%3Abk1/pages/pg2?mastery_template=tpl1'
    )
    expect(bookRoute(null, null, '')).toBe('/learning/books')
    expect(bookErrorMessage(new BookApiError('x', 403), t)).toMatch(
      /^This course is no longer available/
    )
  })
})
