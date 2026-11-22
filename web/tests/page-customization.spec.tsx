import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { MessageSquare, Microscope } from 'lucide-react'
import { initI18n } from '@/i18n/init'
import PageCustomizeDialog from '@/components/customization/PageCustomizeDialog'
import { DEFAULT_PAGE_CUSTOMIZATION, type PageCustomization } from '@/lib/page-customization'
import StarterSuggestions from '@/components/chat/home/StarterSuggestions'
import { LearningWays } from '@/components/learning/LearningWays'

initI18n('en')
const harness = vi.hoisted(() => ({
  save: vi.fn(),
  value: null as PageCustomization | null,
}))
vi.mock('@/context/PageCustomizationContext', () => ({
  usePageCustomization: () => ({
    value: harness.value,
    save: harness.save,
    loading: false,
    error: false,
  }),
}))
vi.mock('@/lib/api', () => ({
  apiUrl: (url: string) => url,
  apiFetch: vi.fn(async () => ({
    ok: true,
    json: async () => ({ suggestions: [], stale: false, status: 'no-material' }),
  })),
}))
vi.mock('@/components/customization/PageCustomizeButton', () => ({
  PageCustomizeButton: () => <button>Customize page</button>,
}))
vi.mock('next/navigation', () => ({
  usePathname: () => '/learning/new',
  useRouter: () => ({ push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}))
vi.mock('@/lib/workspace-scope', () => ({
  activeWorkspaceId: () => 'workspace-a',
  scopedUrl: (url: string) => url,
}))
const capabilities = [
  { value: '', label: 'Chat', description: 'Chat', icon: MessageSquare, allowedTools: [] },
  {
    value: 'deep_research',
    label: 'Research',
    description: 'Research',
    icon: Microscope,
    allowedTools: [],
  },
]
beforeEach(() => {
  harness.value = structuredClone(DEFAULT_PAGE_CUSTOMIZATION)
  harness.save.mockResolvedValue(undefined)
})
function dialog(close = vi.fn()) {
  return render(
    <PageCustomizeDialog
      page="chat"
      initial={harness.value!}
      capabilities={capabilities}
      onClose={close}
    />
  )
}

it('saves selected controls and modes without changing core chat', async () => {
  const close = vi.fn()
  dialog(close)
  expect(screen.queryByRole('switch', { name: /^Chat/ })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('switch', { name: /Research/ }))
  fireEvent.click(screen.getByRole('button', { name: 'Input tools' }))
  fireEvent.click(screen.getByRole('switch', { name: 'Voice input' }))
  fireEvent.click(screen.getByRole('switch', { name: 'MCP connectors' }))
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  await waitFor(() => expect(close).toHaveBeenCalled())
  expect(harness.save).toHaveBeenCalledWith(
    'chat',
    expect.objectContaining({
      hidden_modes: ['deep_research'],
      controls: expect.objectContaining({ voice: false, mcp: false, skills: true }),
    })
  )
})
it('edits prompt titles/content, adds, removes, and restores the translated defaults', () => {
  dialog()
  fireEvent.click(screen.getByRole('button', { name: 'Starting prompts' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'Display title 1' }), {
    target: { value: 'My starter' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Add prompt' }))
  expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'Remove prompt 5' }))
  expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled()
  fireEvent.click(screen.getByRole('button', { name: 'Restore this category' }))
  expect(screen.getByRole('textbox', { name: 'Display title 1' })).not.toHaveValue('My starter')
})
it('keeps the draft on save failure and does not save on cancel', async () => {
  const close = vi.fn()
  harness.save.mockRejectedValueOnce(new Error('offline'))
  dialog(close)
  fireEvent.click(screen.getByRole('button', { name: 'Input tools' }))
  fireEvent.click(screen.getByRole('switch', { name: 'Voice input' }))
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  await screen.findByRole('alert')
  expect(screen.getByRole('switch', { name: 'Voice input' })).not.toBeChecked()
  expect(close).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
  expect(close).toHaveBeenCalledOnce()
  expect(harness.save).toHaveBeenCalledOnce()
})
it('uses custom starting prompts literally and hides an empty category', async () => {
  harness.value!.chat.starters = {
    explain: [{ label: 'Personal title', prompt: 'Private customized content' }],
    solve: [],
  }
  const pick = vi.fn()
  const prefill = vi.fn()
  await act(async () => {
    render(<StarterSuggestions onPick={pick} onPrefill={prefill} />)
  })
  expect(screen.queryByText('Solve')).not.toBeInTheDocument()
  fireEvent.click(screen.getByText('Explain'))
  fireEvent.click(screen.getByRole('button', { name: 'Personal title' }))
  expect(prefill).toHaveBeenCalledWith('Private customized content')
  expect(pick).not.toHaveBeenCalled()
})
it('hides the selected method while keeping the other entry points', () => {
  harness.value!.learning.hidden_features = ['mastery']
  render(<LearningWays />)
  expect(screen.queryByRole('button', { name: /Mastery Path/ })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: /^Books/ })).toBeInTheDocument()
})
it('leaves a way to restore choices when every learning method is hidden', () => {
  harness.value!.learning.hidden_features = ['books', 'mastery', 'practice', 'reading']
  render(<LearningWays />)
  expect(screen.getAllByRole('button')).toEqual([screen.getByRole('button', { name: 'Customize page' })])
})
