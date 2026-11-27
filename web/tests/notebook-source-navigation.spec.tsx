import { act, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import NotebookConsole from '@/components/notebook/NotebookConsole'

const fixture = vi.hoisted(() => ({
  replace: vi.fn(),
  push: vi.fn(),
  locate: vi.fn(),
  list: vi.fn(),
  get: vi.fn(),
  t: (key: string) => key,
}))
vi.mock('next/navigation', () => ({
  useRouter: () => fixture,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: fixture.t }),
}))
vi.mock('@/lib/notebook-api', () => ({
  listNotebooks: fixture.list,
  getNotebook: fixture.get,
  locateFavoriteRecord: fixture.locate,
  createNotebook: vi.fn(),
  deleteNotebook: vi.fn(),
  deleteNotebookRecord: vi.fn(),
  relocateNotebookRecord: vi.fn(),
  updateNotebook: vi.fn(),
  updateNotebookRecord: vi.fn(),
  exportNotebookMarkdown: vi.fn(),
}))
vi.mock('@/components/notebook/NotebookRecordRow', () => ({
  default: ({ record, expanded }: { record: { id: string; title: string }; expanded: boolean }) => (
    <div data-testid={record.id} data-expanded={expanded}>
      {record.title}
    </div>
  ),
}))

const source = { id: 'source', name: 'Source', records: [], record_count: 0 }
const record = { id: 'favorite', title: 'Saved source', type: 'chat', output: 'Original' }
const target = { id: 'target', name: 'Target', records: [record], record_count: 1 }
const course = { id: 'course-one', name: 'Course', notebookIds: ['source', 'target'] }

beforeEach(() => {
  vi.clearAllMocks()
  window.history.replaceState(
    {},
    '',
    '/kaoyan/notebooks/source?record=favorite&dt_workspace=research'
  )
  fixture.list.mockResolvedValue([source, target])
  fixture.get.mockImplementation(async (id: string) => {
    if (id === 'source') return source
    if (id === 'target') return target
    throw new Error('Collection removed')
  })
  fixture.locate.mockResolvedValue('target')
})

it('finds a moved source and retains the record, course and workspace in navigation', async () => {
  const view = render(
    <NotebookConsole initialNotebookId="source" initialRecordId="favorite" courseScope={course} />
  )
  await waitFor(() => expect(fixture.locate).toHaveBeenCalledWith('favorite', 'source'))
  await waitFor(() => expect(fixture.replace).toHaveBeenCalledOnce())
  const destination = new URL(fixture.replace.mock.calls[0][0], 'http://localhost')
  expect(destination.pathname).toBe('/notebooks/target')
  expect(Object.fromEntries(destination.searchParams)).toEqual({
    record: 'favorite',
    course: 'course-one',
    dt_workspace: 'research',
  })
  view.rerender(
    <NotebookConsole initialNotebookId="target" initialRecordId="favorite" courseScope={course} />
  )
  await waitFor(() =>
    expect(screen.getByTestId('favorite')).toHaveAttribute('data-expanded', 'true')
  )
})

it('resolves a source even when its old collection has been removed', async () => {
  fixture.list.mockResolvedValue([target])
  fixture.get.mockImplementation(async (id: string) => {
    if (id === 'target') return target
    throw new Error('Collection removed')
  })
  render(<NotebookConsole initialNotebookId="source" initialRecordId="favorite" />)
  await waitFor(() => expect(fixture.locate).toHaveBeenCalledWith('favorite', 'source'))
  await waitFor(() => expect(fixture.replace).toHaveBeenCalledOnce())
  expect(fixture.replace.mock.calls[0][0]).toContain('record=favorite')
})

it('does not redirect to a collection outside the current course', async () => {
  render(
    <NotebookConsole
      initialNotebookId="source"
      initialRecordId="favorite"
      courseScope={{ ...course, notebookIds: ['source'] }}
    />
  )
  expect(await screen.findByText('This favorite is no longer available.')).toBeVisible()
  expect(fixture.replace).not.toHaveBeenCalled()
})

it.each([
  ['deleted', null, 'This favorite is no longer available.'],
  [
    'failed lookup',
    new Error('Offline'),
    'Could not locate this favorite. Try reopening the source link.',
  ],
])('reports %s sources without opening another record', async (_case, result, message) => {
  if (result instanceof Error) fixture.locate.mockRejectedValue(result)
  else fixture.locate.mockResolvedValue(result)
  render(<NotebookConsole initialNotebookId="source" initialRecordId="favorite" />)
  expect(await screen.findByText(message)).toBeVisible()
  expect(fixture.replace).not.toHaveBeenCalled()
})

it('ignores an old lookup after a different source is opened', async () => {
  const pending = Promise.withResolvers<string>()
  fixture.locate.mockReturnValue(pending.promise)
  const view = render(<NotebookConsole initialNotebookId="source" initialRecordId="favorite" />)
  await waitFor(() => expect(fixture.locate).toHaveBeenCalled())
  view.rerender(<NotebookConsole initialNotebookId="target" initialRecordId="favorite" />)
  await waitFor(() => expect(screen.getByTestId('favorite')).toBeVisible())
  await act(async () => pending.resolve('unrelated'))
  expect(fixture.replace.mock.calls.some(([url]) => url.includes('unrelated'))).toBe(false)
})
