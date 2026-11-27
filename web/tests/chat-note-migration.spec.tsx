import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import ChatMarkdownNoteTab from '@/components/chat/home/ChatMarkdownNoteTab'
import { saveChatMarkdownNoteDraft } from '@/lib/chat-markdown-note'

const fixture = vi.hoisted(() => ({
  userId: 'alice',
  create: vi.fn(),
}))

vi.mock('@/hooks/useAuthStatus', () => ({
  useAuthStatus: () => ({
    loading: false, statusAvailable: true, enabled: true, userId: fixture.userId,
  }),
}))
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }))
vi.mock('@/lib/notes-api', () => ({ createNote: fixture.create }))
vi.mock('next/dynamic', () => ({
  default: () => ({ docId }: { docId: string }) => <div data-testid="note">{docId}</div>,
}))

beforeEach(() => {
  localStorage.clear()
  fixture.userId = 'alice'
  fixture.create.mockReset()
})

it('recovers an unsaved legacy draft as a new page without overwriting its saved page', async () => {
  saveChatMarkdownNoteDraft('alice', 'chat', {
    title: 'Edited title', content: 'Unsaved text',
    saved: { id: 'original', title: 'Original', content: 'Saved text' },
  })
  fixture.create.mockResolvedValue({ id: 'recovered', title: 'Edited title', content: 'Unsaved text' })
  render(<ChatMarkdownNoteTab sessionId="chat" />)
  fireEvent.click(await screen.findByRole('button', { name: 'Save draft as a new page' }))
  await waitFor(() => expect(screen.getByTestId('note')).toHaveTextContent('recovered'))
  expect(fixture.create).toHaveBeenCalledWith('Edited title', null, 'Unsaved text')
})

it('does not display the previous account or conversation note after switching scope', async () => {
  saveChatMarkdownNoteDraft('alice', 'chat', {
    title: 'Alice', content: 'Private',
    saved: { id: 'alice-note', title: 'Alice', content: 'Private' },
  })
  const view = render(<ChatMarkdownNoteTab sessionId="chat" />)
  expect(await screen.findByTestId('note')).toHaveTextContent('alice-note')
  fixture.userId = 'bob'
  view.rerender(<ChatMarkdownNoteTab sessionId="chat" />)
  expect(screen.queryByTestId('note')).toBeNull()
  expect(screen.getByRole('button', { name: 'Create a note' })).toBeInTheDocument()
  fixture.userId = 'alice'
  view.rerender(<ChatMarkdownNoteTab sessionId="other-chat" />)
  expect(screen.queryByTestId('note')).toBeNull()
})
