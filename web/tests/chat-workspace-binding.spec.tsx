import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { WorkspacePill } from '@/components/workspaces/WorkspacePill'
import { useWorkspaceBinding } from '@/hooks/useWorkspaceBinding'
import type { ChatWorkspaceRegistration } from '@/lib/workspaces-api'
import WorkspaceSettingsSection from '@/features/settings/sections/WorkspaceSettingsSection'
import { buildStartTurnInput } from '@/features/chat/controllers/buildStartTurnInput'

const fixtures = vi.hoisted(() => ({
  state: {
    sessionKey: 'draft-a',
    sessionId: null as string | null,
    workspaceId: null as string | null,
    isStreaming: false,
  },
  configure: vi.fn(),
  list: vi.fn(),
  save: vi.fn(),
  move: vi.fn(),
  catalog: vi.fn(),
  migrate: vi.fn(),
  snapshot: vi.fn(),
  navigateWorkspace: vi.fn(),
}))
vi.mock('@/lib/workspace-scope', () => ({ selectWorkspace: fixtures.navigateWorkspace }))
vi.mock('@/features/chat/ChatStateAdapter', () => ({
  useChatStateAdapter: () => ({ state: fixtures.state, configureSession: fixtures.configure }),
}))
vi.mock('@/lib/workspaces-api', () => ({
  inheritedWorkspaceResources: () => ({ skills: null, mcp: null, knowledge_bases: null }),
  getWorkspaceResources: async () => ({ skills: [], mcp: [], knowledge_bases: [] }),
  listWorkspaces: fixtures.list,
  getWorkspaceCatalog: fixtures.catalog,
  migrateWorkspace: fixtures.migrate,
  getSystemWorkspaceSnapshot: fixtures.snapshot,
  workspaceLabel: (row: { display_name: string }) => row.display_name,
  saveWorkspace: fixtures.save,
  workspaceChatHref: (id: string) => `/chat?workspace=${id}`,
}))
vi.mock('@/lib/session-api', () => ({ updateSessionOrganization: fixtures.move }))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'en' } }),
}))
const workspace: ChatWorkspaceRegistration = {
  workspace_id: 'ws_a',
  kind: 'workspace',
  follows_root: true,
  display_name: 'Algebra',
  path: '/materials/algebra',
  archived: false,
  status: 'ready',
  error: '',
  created_at: '',
}
function BindingHarness() {
  const binding = useWorkspaceBinding(fixtures.state, fixtures.configure)
  return <WorkspacePill workspaces={[workspace]} workspaceId={fixtures.state.workspaceId || ''} onSelect={binding.selectWorkspace} error={binding.error} disabled={fixtures.state.isStreaming || binding.pending} />
}
function openPicker() { fireEvent.click(screen.getByRole('button', { name: 'Conversation workspace' })) }
function pickAlgebra() { openPicker(); fireEvent.click(screen.getByRole('menuitemradio', { name: 'Algebra' })) }
beforeEach(() => {
  vi.clearAllMocks()
  window.history.replaceState(null, '', '/chat')
  fixtures.state = { sessionKey: 'draft-a', sessionId: null, workspaceId: null, isStreaming: false }
  fixtures.list.mockResolvedValue([workspace])
  fixtures.catalog.mockResolvedValue({ root: '/root', workspaces: [workspace] })
  fixtures.migrate.mockResolvedValue({ root: '/new-root', workspaces: [workspace] })
  fixtures.save.mockResolvedValue(workspace)
  fixtures.move.mockResolvedValue({})
})
afterEach(cleanup)

it('binds a draft and preserves omitted versus explicit null on the wire', async () => {
  render(<BindingHarness />)
  pickAlgebra()
  expect(fixtures.configure).not.toHaveBeenCalled()
  expect(fixtures.move).not.toHaveBeenCalled()
  expect(fixtures.navigateWorkspace).toHaveBeenCalledWith('ws_a', '/chat', expect.objectContaining({ beforeNavigate: expect.any(Function) }))
  expect(buildStartTurnInput({ content: 'hello', workspaceId: 'ws_a' }).workspace_id).toBe('ws_a')
  expect(buildStartTurnInput({ content: 'hello', workspaceId: null }).workspace_id).toBeNull()
  expect(buildStartTurnInput({ content: 'continue', sessionId: 'existing' })).not.toHaveProperty(
    'workspace_id'
  )
})
it('persists a move before updating the existing session UI', async () => {
  fixtures.state.sessionId = 'existing'
  fixtures.state.workspaceId = 'ws_a'
  render(<BindingHarness />)
  openPicker()
  fireEvent.click(screen.getByRole('button', { name: 'Default workspace' }))
  await waitFor(() =>
    expect(fixtures.configure).toHaveBeenCalledWith({ workspaceId: null }, 'draft-a')
  )
  expect(fixtures.move).toHaveBeenCalledWith('existing', { workspace_id: null })
})
it('keeps the source session key if the user switches while a move is pending', async () => {
  fixtures.state.sessionId = 'existing'
  let resolve!: () => void
  fixtures.move.mockReturnValue(
    new Promise<void>(done => {
      resolve = done
    })
  )
  render(<BindingHarness />)
  pickAlgebra()
  fixtures.state = { ...fixtures.state, sessionKey: 'draft-b', sessionId: 'other' }
  await act(async () => resolve())
  expect(fixtures.configure).toHaveBeenCalledWith({ workspaceId: 'ws_a' }, 'draft-a')
})
it('keeps ownership on failure and disables moving a running turn', async () => {
  fixtures.state.sessionId = 'existing'
  fixtures.move.mockRejectedValue(new Error('Turn is running'))
  const { rerender } = render(<BindingHarness />)
  pickAlgebra()
  expect(await screen.findByRole('alert')).toHaveTextContent('Turn is running')
  expect(fixtures.configure).not.toHaveBeenCalled()
  fixtures.state.isStreaming = true
  rerender(<BindingHarness />)
  expect(screen.getByRole('button', { name: 'Conversation workspace' })).toBeDisabled()
})
it('creates managed workspaces without a server folder field', async () => {
  render(<WorkspaceSettingsSection />)
  await screen.findByText('/materials/algebra')
  fireEvent.click(screen.getByRole('button', { name: 'New workspace' }))
  fireEvent.change(screen.getByPlaceholderText('e.g. Probability'), {
    target: { value: 'Geometry' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Create workspace' }))
  await waitFor(() => expect(fixtures.save).toHaveBeenCalledWith({ name: 'Geometry', resources: { skills: null, mcp: null, knowledge_bases: null } }))
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Create workspace' })).toBeNull())
  // Spec 2026-10-29 P1c: no server folder can be entered.
  fireEvent.click(screen.getByRole('button', { name: 'New workspace' }))
  expect(screen.queryByPlaceholderText('Leave empty to create under root')).toBeNull()
})

it('offers no storage-root or folder migration controls', async () => {
  // Spec 2026-10-29 P1c: DeepTutor decides every storage location.
  render(<WorkspaceSettingsSection />)
  await screen.findByText('Algebra')
  expect(screen.queryByLabelText('Workspace root folder')).toBeNull()
  expect(screen.queryByRole('button', { name: 'Move folder' })).toBeNull()
})

it('hides system, general, archived and invalid workspaces as new chat destinations', () => {
  render(<WorkspacePill workspaces={[
    { ...workspace, workspace_id: 'system', display_name: 'System', kind: 'system' },
    { ...workspace, workspace_id: 'general', display_name: 'General', kind: 'general' },
    { ...workspace, workspace_id: 'archive', display_name: 'Archive', archived: true },
    { ...workspace, workspace_id: 'invalid', display_name: 'Invalid', status: 'invalid' },
    workspace,
  ]} workspaceId="ws_a" onSelect={fixtures.configure} />)
  openPicker()
  expect(screen.getByRole('button', { name: 'Default workspace' })).toBeInTheDocument()
  expect(screen.getByRole('menuitemradio', { name: 'Invalid' })).toBeDisabled()
  expect(screen.queryByRole('menuitemradio', { name: 'System' })).toBeNull()
  expect(screen.queryByRole('menuitemradio', { name: 'General' })).toBeNull()
  expect(screen.queryByRole('menuitemradio', { name: 'Archive' })).toBeNull()
})

it('blocks duplicate moves while persistence is pending', async () => {
  fixtures.state.sessionId = 'existing'
  let resolve!: () => void
  fixtures.move.mockReturnValue(new Promise<void>(done => { resolve = done }))
  render(<BindingHarness />)
  pickAlgebra()
  expect(screen.getByRole('button', { name: 'Conversation workspace' })).toBeDisabled()
  expect(fixtures.configure).not.toHaveBeenCalled()
  await act(async () => resolve())
  expect(fixtures.configure).toHaveBeenCalledOnce()
})

it('renames and archives a workspace through its row actions', async () => {
  render(<WorkspaceSettingsSection />)
  await screen.findByText('Algebra')
  fireEvent.click(screen.getByRole('button', { name: 'Rename workspace' }))
  fireEvent.change(screen.getByLabelText('Workspace name'), { target: { value: 'Geometry' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  await waitFor(() => expect(fixtures.save).toHaveBeenCalledWith({ name: 'Geometry' }, 'ws_a'))
  await waitFor(() => expect(screen.queryByLabelText('Workspace name')).toBeNull())
  fireEvent.click(screen.getByRole('button', { name: 'Archive workspace' }))
  await waitFor(() => expect(fixtures.save).toHaveBeenCalledWith({ archived: true }, 'ws_a'))
})

it('saves an explicit empty resource selection without changing other resource types', async () => {
  render(<WorkspaceSettingsSection />)
  fireEvent.click(await screen.findByRole('button', { name: 'Assigned resources' }))
  fireEvent.change(screen.getAllByRole('combobox')[0], { target: { value: 'selected' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  await waitFor(() => expect(fixtures.save).toHaveBeenCalledWith({ resources: { skills: [], mcp: null, knowledge_bases: null } }, 'ws_a'))
})
