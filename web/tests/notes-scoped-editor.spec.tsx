import { afterAll, beforeEach, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import type { NotePage } from '@/lib/notes-api'

const previousBasePath = vi.hoisted(() => {
  const previous = process.env.NEXT_PUBLIC_BASE_PATH
  process.env.NEXT_PUBLIC_BASE_PATH = '/kaoyan'
  return previous
})
import NoteEditor from '@/features/notes/components/NoteEditor'
import NoteFavoriteBlock from '@/features/notes/components/NoteFavoriteBlock'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'en' } }),
}))
afterAll(() => {
  if (previousBasePath === undefined) delete process.env.NEXT_PUBLIC_BASE_PATH
  else process.env.NEXT_PUBLIC_BASE_PATH = previousBasePath
})
beforeEach(() => {
  window.history.replaceState({}, '', '/kaoyan/co-writer/abcdef123456?dt_workspace=research')
})

it('renders persisted upload media through the scoped API path without doubling basePath', async () => {
  const raw = '/api/documents/abcdef123456/assets/abc.png?dt_workspace=research'
  const page: NotePage = {
    id: 'abcdef123456', title: 'Media', content: '', revision: 1,
    parent_id: null, icon: '', cover: '', pinned: false, created_at: 0, updated_at: 0,
    blocks: [
      { id: 'raw-image', type: 'image', props: { url: raw, name: 'Raw image' } },
      { id: 'prefixed-image', type: 'image', props: { url: `/kaoyan${raw}`, name: 'Prefixed image' } },
      { id: 'audio', type: 'audio', props: { url: raw.replace('.png', '.mp3'), name: 'Audio' } },
      { id: 'video', type: 'video', props: { url: raw.replace('.png', '.mp4'), name: 'Video' } },
    ],
  }
  const { container } = render(<NoteEditor
    page={page} onChange={vi.fn()} onReady={vi.fn()} onAI={vi.fn()}
    suggestions={[]} reviewBusy={false} onReview={vi.fn()} onDiscuss={vi.fn()}
    onContinueResearch={vi.fn()} threads={[]} activeThread={null} threadsInteractive={false}
    onOpenThread={vi.fn()} onDeleteThread={vi.fn()}
  />)
  await waitFor(() => {
    expect(screen.getByAltText('Raw image')).toHaveAttribute('src', `/kaoyan${raw}`)
    expect(screen.getByAltText('Prefixed image')).toHaveAttribute('src', `/kaoyan${raw}`)
    expect(container.querySelector('audio')).toHaveAttribute('src', `/kaoyan${raw.replace('.png', '.mp3')}`)
    expect(container.querySelector('video')).toHaveAttribute('src', `/kaoyan${raw.replace('.png', '.mp4')}`)
  })
})

it.each([
  ['/notebooks/inbox?record=one', '/kaoyan/notebooks/inbox?record=one&dt_workspace=research'],
  ['/notebooks/inbox?record=one&dt_workspace=other', '/kaoyan/notebooks/inbox?record=one&dt_workspace=other'],
])('keeps favorite source scope: %s', (sourceUrl, expected) => {
  render(<NoteFavoriteBlock
    title="Favorite" markdown="Original" sourceUrl={sourceUrl}
    type="chat" summary="" metadata="{}" expanded={false} onExpanded={vi.fn()}
  />)
  expect(screen.getByRole('link', { name: 'Return to source' })).toHaveAttribute('href', expected)
})
