import { act, render, screen } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import NotebookPage from '@/app/(utility)/notebooks/NotebooksRoute'

const fixture = vi.hoisted(() => ({
  courses: vi.fn(),
  query: new URLSearchParams('course=course-one&record=favorite&dt_workspace=research'),
}))
vi.mock('next/navigation', () => ({
  useParams: () => ({ notebookId: 'source' }),
  useSearchParams: () => fixture.query,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('@/lib/courses-api', () => ({ listCourses: fixture.courses }))
vi.mock('@/components/notebook/NotebookConsole', () => ({
  default: ({ courseScope }: { courseScope: unknown }) => (
    <div data-testid="console">{JSON.stringify(courseScope)}</div>
  ),
}))
beforeEach(() => {
  fixture.courses.mockReset()
})

it('waits for the course scope before resolving a favorite source', async () => {
  const pending = Promise.withResolvers<unknown[]>()
  fixture.courses.mockReturnValue(pending.promise)
  render(<NotebookPage />)
  expect(screen.queryByTestId('console')).toBeNull()
  await act(async () =>
    pending.resolve([
      {
        id: 'course-one',
        name: 'Course',
        resources: [{ kind: 'notebook', ref_id: 'source' }],
      },
    ])
  )
  expect(screen.getByTestId('console')).toHaveTextContent('"notebookIds":["source"]')
})

it('does not broaden the scope to every collection when loading the course fails', async () => {
  fixture.courses.mockRejectedValue(new Error('Offline'))
  render(<NotebookPage />)
  expect(await screen.findByRole('alert')).toHaveTextContent('Offline')
  expect(screen.queryByTestId('console')).toBeNull()
})
