import { useEffect, useState } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import NoteWorkspace from '@/features/notes/components/NoteWorkspace'
import {
  getNote,
  listNotes,
  saveNote,
  getSuggestions,
  noteRequest,
  reviewSuggestion,
  createNote,
} from '@/lib/notes-api'
import { noteDraftKeys } from '@/features/notes/model/drafts'
import type { NotePage, NoteSuggestion } from '@/lib/notes-api'
const navigation = vi.hoisted(() => ({ push: vi.fn() }))
vi.mock('next/navigation', () => ({ useRouter: () => navigation }))
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }))
vi.mock('@/hooks/useAuthStatus', () => ({
  useAuthStatus: () => ({ statusAvailable: true, loading: false, enabled: false }),
}))
vi.mock('@/lib/notes-api', () => ({
  getNote: vi.fn(),
  listNotes: vi.fn(),
  saveNote: vi.fn(),
  getSuggestions: vi.fn(),
  noteRequest: vi.fn(),
  reviewSuggestion: vi.fn(),
  exportNoteFlashcards: vi.fn(),
  createNote: vi.fn(),
}))
// The page's conversations run in the chat runtime; autosave does not.
vi.mock('@/features/notes/components/useNoteThreads', () => ({
  useNoteThreads: () => ({
    threads: [],
    open: null,
    bound: false,
    hasMessages: false,
    prefill: null,
    send: vi.fn(),
    openThread: vi.fn(),
    startThread: vi.fn(),
    generateBlock: vi.fn(),
    regenerate: vi.fn(),
    removeThread: vi.fn(),
    close: vi.fn(),
  }),
}))
vi.mock('@/features/notes/components/NoteEditor', () => ({
  default: function MockEditor({
    onReady,
    onChange,
    page,
    suggestions,
    onReview,
    reviewErrors,
  }: {
    onReady: (value: unknown) => void
    onChange: (value: NotePage['blocks']) => void
    page: NotePage
    suggestions: NoteSuggestion[]
    onReview: (suggestion: NoteSuggestion, accept: boolean) => void
    reviewErrors: Record<string, string>
  }) {
    const [blocks, setBlocks] = useState(page.blocks)
    useEffect(() => {
      onReady({ setEditable: vi.fn(), replace: setBlocks })
    }, [])
    return (
      <div>
        Editor{' '}
        <button
          onClick={() =>
            onChange([{ id: 'edited', type: 'paragraph', props: {}, content: 'New content' }])
          }
        >
          Edit block
        </button>
        <button onClick={() => {
          const next = [...(blocks || []), { id: 'typed', type: 'paragraph', props: {}, content: 'Typed' }]
          setBlocks(next)
          onChange(next)
        }}>Append text</button>
        <output data-testid="editor-snapshot">{JSON.stringify(page.blocks)}</output>
        {suggestions.map(s => (
          <section key={s.id}>
            <button onClick={() => onReview(s, true)}>Accept {s.id}</button>
            <button onClick={() => onReview(s, false)}>Dismiss {s.id}</button>
            {reviewErrors[s.id] && <p role="alert">{reviewErrors[s.id]}</p>}
          </section>
        ))}
      </div>
    )
  },
}))
const page: NotePage = {
  id: 'abcdef123456',
  title: 'Initial',
  content: 'Body',
  blocks: [{ id: 'a', type: 'paragraph', props: {}, content: 'Body' }],
  parent_id: null,
  icon: '',
  cover: '',
  pinned: false,
  revision: 1,
  created_at: 1,
  updated_at: 1,
}
beforeEach(() => {
  localStorage.clear()
  window.history.replaceState({}, '', '/co-writer/abcdef123456')
  vi.mocked(getNote).mockResolvedValue({ ...page })
  vi.mocked(listNotes).mockResolvedValue({ documents: [{ ...page, preview: 'Body' }] })
  vi.mocked(getSuggestions).mockResolvedValue({ suggestions: [] })
  vi.mocked(noteRequest).mockResolvedValue({ pages: [] })
})

const proposal: NoteSuggestion = {
  id: 's1',
  operation: 'replace',
  block_id: 'a',
  markdown: '',
  explanation: '',
  original: page.blocks![0],
  blocks: [{ ...page.blocks![0], content: 'Accepted' }],
  status: 'pending',
}
it('flushes a newly typed edit before approval and retains the returned content on remount', async () => {
  let finish!: (value: NotePage) => void
  vi.mocked(getSuggestions).mockResolvedValue({ suggestions: [proposal] })
  vi.mocked(saveNote).mockImplementationOnce(
    () =>
      new Promise(resolve => {
        finish = resolve
      })
  )
  const accepted = {
    ...page,
    blocks: [
      ...proposal.blocks!,
      { id: 'edited', type: 'paragraph', props: {}, content: 'New content' },
    ],
    revision: 3,
  }
  vi.mocked(reviewSuggestion).mockResolvedValue(accepted)
  const ui = render(<NoteWorkspace docId={page.id} />)
  fireEvent.click(await screen.findByRole('button', { name: 'Edit block' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Accept s1' }))
  await waitFor(() => expect(saveNote).toHaveBeenCalledTimes(1))
  expect(reviewSuggestion).not.toHaveBeenCalled()
  finish({ ...page, revision: 2 })
  await waitFor(() =>
    expect(reviewSuggestion).toHaveBeenCalledWith(page.id, 's1', true, proposal.blocks)
  )
  expect(screen.getByTestId('editor-snapshot')).toHaveTextContent('Accepted')
  expect(screen.getByTestId('editor-snapshot')).toHaveTextContent('New content')
  ui.unmount()
  vi.mocked(getNote).mockResolvedValue(accepted)
  vi.mocked(getSuggestions).mockResolvedValue({ suggestions: [] })
  render(<NoteWorkspace docId={page.id} />)
  expect(await screen.findByTestId('editor-snapshot')).toHaveTextContent('Accepted')
  expect(screen.getByTestId('editor-snapshot')).toHaveTextContent('New content')
})
it('keeps conflicting edits intact and lets the user dismiss the suggestion locally', async () => {
  vi.mocked(getSuggestions).mockResolvedValue({ suggestions: [proposal] })
  vi.mocked(reviewSuggestion)
    .mockRejectedValueOnce(
      new Error('The suggested block changed. Ask Dito to revise the suggestion.')
    )
    .mockResolvedValueOnce(page)
  render(<NoteWorkspace docId={page.id} />)
  fireEvent.click(await screen.findByRole('button', { name: 'Accept s1' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Your edits are safe')
  expect(screen.getByTestId('editor-snapshot')).toHaveTextContent('Body')
  vi.mocked(getSuggestions).mockResolvedValue({ suggestions: [] })
  fireEvent.click(screen.getByRole('button', { name: 'Dismiss s1' }))
  await waitFor(() =>
    expect(screen.queryByRole('button', { name: 'Accept s1' })).not.toBeInTheDocument()
  )
  expect(reviewSuggestion).toHaveBeenLastCalledWith(page.id, 's1', false, undefined)
  expect(screen.getByTestId('editor-snapshot')).toHaveTextContent('Body')
})
it('passes current blocks back to the editor so remounts cannot restore stale content', async () => {
  vi.mocked(saveNote).mockResolvedValue({ ...page, revision: 2 })
  render(<NoteWorkspace docId={page.id} />)
  fireEvent.click(await screen.findByRole('button', { name: 'Edit block' }))
  expect(screen.getByTestId('editor-snapshot')).toHaveTextContent('New content')
  await waitFor(() => expect(saveNote).toHaveBeenCalledTimes(1), { timeout: 3000 })
  expect(vi.mocked(saveNote).mock.calls[0][1].blocks?.[0].id).toBe('edited')
  expect(screen.getByTestId('editor-snapshot')).toHaveTextContent('New content')
})
it('serializes edits made during an in-flight save against the returned revision', async () => {
  let finish!: (value: NotePage) => void
  vi.mocked(saveNote)
    .mockImplementationOnce(
      () =>
        new Promise(resolve => {
          finish = resolve
        })
    )
    .mockResolvedValueOnce({ ...page, title: 'Second', revision: 3 })
  render(<NoteWorkspace docId={page.id} />)
  const title = await screen.findByRole('textbox', { name: 'Page title' })
  fireEvent.change(title, { target: { value: 'First' } })
  await waitFor(() => expect(saveNote).toHaveBeenCalledTimes(1), { timeout: 3000 })
  fireEvent.change(title, { target: { value: 'Second' } })
  finish({ ...page, title: 'First', revision: 2 })
  await waitFor(() => expect(saveNote).toHaveBeenCalledTimes(2))
  expect(vi.mocked(saveNote).mock.calls[1][1]).toMatchObject({
    title: 'Second',
    expected_revision: 2,
  })
  await waitFor(() => expect(screen.getByText('Saved')).toBeInTheDocument())
  expect(title).toHaveValue('Second')
})
it('keeps a recovery draft on conflict instead of overwriting a newer page', async () => {
  vi.mocked(saveNote).mockRejectedValue(new Error('This page changed elsewhere'))
  render(<NoteWorkspace docId={page.id} />)
  fireEvent.change(await screen.findByRole('textbox', { name: 'Page title' }), {
    target: { value: 'Unsaved' },
  })
  await waitFor(
    () => expect(screen.getByRole('alert')).toHaveTextContent('This page changed elsewhere'),
    { timeout: 3000 }
  )
  expect(localStorage.getItem(noteDraftKeys('local', '', page.id).draft)).toContain('Unsaved')
})

it.each([false, true])('syncs server blocks before typing after comment review (accept=%s)', async accept => {
  const comment = { ...proposal, operation: 'comment' as const }
  vi.mocked(getSuggestions).mockResolvedValue({ suggestions: [comment] })
  const remote = { ...page, revision: 2, blocks: [{ ...page.blocks![0], content: 'Other tab text' }] }
  vi.mocked(reviewSuggestion).mockResolvedValue(remote)
  vi.mocked(saveNote).mockImplementation(async (_, patch) => ({ ...remote, ...patch, revision: 3 }))
  render(<NoteWorkspace docId={page.id} />)
  fireEvent.click(await screen.findByRole('button', { name: `${accept ? 'Accept' : 'Dismiss'} s1` }))
  await waitFor(() => expect(reviewSuggestion).toHaveBeenCalled())
  await waitFor(() => expect(screen.getByTestId('editor-snapshot')).toHaveTextContent('Other tab text'))
  fireEvent.click(screen.getByRole('button', { name: 'Append text' }))
  await waitFor(() => expect(saveNote).toHaveBeenCalledTimes(1), { timeout: 3000 })
  expect(vi.mocked(saveNote).mock.calls[0][1]).toMatchObject({
    expected_revision: 2,
    blocks: [expect.objectContaining({ content: 'Other tab text' }), expect.objectContaining({ content: 'Typed' })],
  })
})

it('preserves unresolved conflicting drafts through autosave and remount until recovery', async () => {
  const keys = noteDraftKeys('local', '', page.id)
  localStorage.setItem(`deeptutor.notes.draft:local:${page.id}`, JSON.stringify({
    title: 'Earlier draft', blocks: [{ ...page.blocks![0], content: 'Only local copy' }], revision: 0,
  }))
  vi.mocked(saveNote).mockImplementation(async (_, patch) => ({ ...page, ...patch, revision: 2 }))
  vi.mocked(createNote).mockResolvedValue({ ...page, id: 'fedcba654321', revision: 0 })
  const ui = render(<NoteWorkspace docId={page.id} />)
  await screen.findByRole('button', { name: 'Save draft as a new page' })
  fireEvent.change(screen.getByRole('textbox', { name: 'Page title' }), { target: { value: 'New title' } })
  await waitFor(() => expect(saveNote).toHaveBeenCalledTimes(1), { timeout: 3000 })
  expect(localStorage.getItem(keys.recovery)).toContain('Only local copy')
  ui.unmount()
  vi.mocked(getNote).mockResolvedValue({ ...page, title: 'New title', revision: 2 })
  render(<NoteWorkspace docId={page.id} />)
  fireEvent.click(await screen.findByRole('button', { name: 'Save draft as a new page' }))
  await waitFor(() => expect(saveNote).toHaveBeenCalledWith('fedcba654321', expect.objectContaining({
    blocks: [expect.objectContaining({ content: 'Only local copy' })],
  })))
  await waitFor(() => expect(localStorage.getItem(keys.recovery)).toBeNull())
})

it('preserves the workspace when using the notebook page selector', async () => {
  window.history.replaceState({}, '', `/co-writer/${page.id}?dt_workspace=research`)
  vi.mocked(listNotes).mockResolvedValue({ documents: [
    { ...page, preview: '' }, { ...page, id: 'fedcba654321', title: 'Second', preview: '' },
  ] })
  render(<NoteWorkspace docId={page.id} />)
  await screen.findByRole('option', { name: /Second/ })
  fireEvent.change(screen.getByRole('combobox', { name: 'Notebook pages' }), { target: { value: 'fedcba654321' } })
  await waitFor(() => expect(navigation.push).toHaveBeenCalledWith('/co-writer/fedcba654321?dt_workspace=research'))
})

it('retains recovery data when saving its new page fails, and supports explicit discard', async () => {
  const keys = noteDraftKeys('local', '', page.id)
  localStorage.setItem(keys.recovery, JSON.stringify([{
    title: 'Earlier draft', blocks: page.blocks, revision: 0,
  }]))
  vi.mocked(createNote).mockResolvedValue({ ...page, id: 'fedcba654321', revision: 0 })
  vi.mocked(saveNote).mockRejectedValue(new Error('Offline'))
  render(<NoteWorkspace docId={page.id} />)
  fireEvent.click(await screen.findByRole('button', { name: 'Save draft as a new page' }))
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Offline'))
  expect(localStorage.getItem(keys.recovery)).toContain('Earlier draft')
  fireEvent.click(screen.getByRole('button', { name: 'Discard' }))
  expect(localStorage.getItem(keys.recovery)).toBeNull()
  expect(screen.queryByRole('button', { name: 'Save draft as a new page' })).toBeNull()
})

it('does not adopt legacy browser drafts when the server denies the document', async () => {
  const keys = noteDraftKeys('local', '', page.id)
  const legacy = JSON.stringify({ title: 'Private draft', blocks: page.blocks, revision: 0 })
  localStorage.setItem(keys.legacy, legacy)
  vi.mocked(getNote).mockRejectedValue(new Error('Document not found'))
  render(<NoteWorkspace docId={page.id} />)
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Document not found'))
  expect(localStorage.getItem(keys.legacy)).toBe(legacy)
  expect(localStorage.getItem(keys.draft)).toBeNull()
  expect(localStorage.getItem(keys.recovery)).toBeNull()
  expect(screen.queryByRole('button', { name: 'Save draft as a new page' })).toBeNull()
})
