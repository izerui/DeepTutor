import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import NoteTaskView from '@/features/notes/components/NoteTaskView'
import NoteTaskBlock from '@/features/notes/components/NoteTaskBlock'
import {
  DEFAULT_TASK_COLORS,
  getTaskBoard,
  publishTaskBoard,
  subscribeTaskBoardChanges,
  type TaskBoard,
  type TaskCard,
} from '@/lib/task-board-api'

let workspaceId = 'study'
vi.mock('@/hooks/useActiveWorkspaceId', () => ({
  useActiveWorkspaceId: () => workspaceId,
}))
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }))

function card(id: string, scope: string | null): TaskCard {
  return {
    id, title: id, workspace_id: scope, status: 'todo', archived: false,
    note: '', created_at: '', updated_at: '', due_date: '2026-11-27',
  }
}
let board: TaskBoard
beforeEach(() => {
  workspaceId = 'study'
  window.history.replaceState({}, '', '/co-writer?dt_workspace=study')
  board = {
    cards: [card('Study task', 'study'), card('Private task', 'private'),
      card('Unassigned task', null), card('General task', '')],
    colors: DEFAULT_TASK_COLORS, revision: 1, session_links: [],
  }
  vi.stubGlobal('fetch', vi.fn(async (_url, init?: RequestInit) => {
    if (init?.method === 'POST') {
      const payload = JSON.parse(String(init.body))
      board = { ...board, revision: board.revision + 1,
        cards: [...board.cards, { ...card(payload.title, payload.workspace_id), ...payload }] }
    }
    return Response.json(board)
  }))
})
afterEach(() => vi.unstubAllGlobals())

it('keeps full account reads and subscriptions while scoping initial and updated workspace boards', async () => {
  expect((await getTaskBoard()).cards).toHaveLength(4)
  expect((await getTaskBoard(undefined, 'study')).cards.map(c => c.id)).toEqual(['Study task'])
  expect((await getTaskBoard(undefined, '')).cards.map(c => c.id))
    .toEqual(['Unassigned task', 'General task'])
  const account = vi.fn()
  const study = vi.fn()
  const general = vi.fn()
  const off = [
    subscribeTaskBoardChanges(account, null),
    subscribeTaskBoardChanges(study, 'study'),
    subscribeTaskBoardChanges(general, ''),
  ]
  try {
    publishTaskBoard(board, 'private')
    expect(account.mock.lastCall?.[0].cards).toHaveLength(4)
    expect(study.mock.lastCall?.[0].cards.map((c: TaskCard) => c.id)).toEqual(['Study task'])
    expect(study.mock.lastCall?.[1]).toBe('study')
    expect(general.mock.lastCall?.[0].cards).toHaveLength(2)
    board.cards[0] = { ...board.cards[0], workspace_id: 'private' }
    publishTaskBoard(board, 'private')
    expect(study.mock.lastCall?.[0].cards).toEqual([])
    expect(account.mock.lastCall?.[0].cards).toHaveLength(4)
  } finally {
    off.forEach(stop => stop())
  }
})

it('keeps note task views scoped after updates and assigns new cards to that workspace', async () => {
  render(<NoteTaskView view="list" onViewChange={vi.fn()} />)
  await screen.findByText('Study task')
  expect(screen.queryByText('Private task')).not.toBeInTheDocument()
  act(() => publishTaskBoard(board))
  expect(screen.queryByText('Private task')).not.toBeInTheDocument()
  fireEvent.change(screen.getByRole('textbox', { name: 'New task' }), {
    target: { value: 'Created in study' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Add' }))
  await screen.findByText('Created in study')
  expect(board.cards.at(-1)?.workspace_id).toBe('study')
  act(() => {
    board.cards = board.cards.map(c => c.id === 'Study task' ? { ...c, workspace_id: 'private' } : c)
    publishTaskBoard(board, 'private')
  })
  expect(screen.queryByText('Study task')).not.toBeInTheDocument()
})

it('does not display a linked task from another workspace on load or publication', async () => {
  render(<NoteTaskBlock data='{"taskId":"Private task"}' prompt="" onChange={vi.fn()} />)
  await waitFor(() => expect(fetch).toHaveBeenCalled())
  expect(screen.queryByText('Private task')).not.toBeInTheDocument()
  act(() => publishTaskBoard(board))
  expect(screen.queryByText('Private task')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Task unavailable' })).toBeDisabled()
})
