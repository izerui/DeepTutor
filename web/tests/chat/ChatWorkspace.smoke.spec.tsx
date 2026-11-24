import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { initI18n } from '@/i18n/init'
import type { ChatState, MessageItem } from '@/features/chat/ChatStateAdapter'
import type { StreamEvent } from '@/features/chat/model/protocol'

initI18n('en')

/**
 * Smoke coverage for the chat home surface (DT-21 top-15 gap #2).
 *
 * The page has never been imported by any vitest file, so its streaming
 * state machine and the submission-failure banner had no regression
 * protection. These tests mount the real ChatWorkspace with the
 * ChatStateAdapter mocked at its boundary: state transitions are injected
 * the way the adapter's provider would deliver them (rerender with the
 * next state), while heavy leaf surfaces (composer, message list,
 * pickers via next/dynamic) stay as capturing stubs so the file under
 * test is ChatWorkspace's own wiring.
 */

const fixture = vi.hoisted(() => ({
  adapter: {
    state: null as unknown as ChatState,
    setTools: vi.fn(),
    setCapability: vi.fn(),
    configureSession: vi.fn(),
    setKBs: vi.fn(),
    initializeKBs: vi.fn(),
    setLLMSelection: vi.fn(),
    setPersonaSelection: vi.fn(),
    setResourceSelection: vi.fn(),
    setReplyLanguageOverride: vi.fn(() => Promise.resolve()),
    sendMessage: vi.fn(),
    queueMessage: vi.fn(),
    withdrawQueuedMessage: vi.fn(),
    cancelStreamingTurn: vi.fn(),
    submitUserReply: vi.fn(async () => true),
    regenerateLastMessage: vi.fn(),
    resendLastMessage: vi.fn(),
    deleteTurn: vi.fn(),
    editMessage: vi.fn(),
    switchBranch: vi.fn(),
    newSession: vi.fn(() => 'draft:general'),
    loadSession: vi.fn(async () => []),
    showCachedSession: vi.fn(() => false),
    loadMessageTrace: vi.fn(async () => undefined),
    releaseMessageTrace: vi.fn(),
    renameSessionTitle: vi.fn(async () => undefined),
    setCourseId: vi.fn(),
  },
  routeSessionId: null as string | null,
  listKnowledgeBases: vi.fn(async (): Promise<Array<Record<string, unknown>>> => []),
  composerProps: null as Record<string, unknown> | null,
  listProps: null as {
    messages: MessageItem[]
    isStreaming: boolean
    canResendLastTurn: boolean
  } | null,
  setActiveSessionId: vi.fn(),
}))

vi.mock('@/features/chat/ChatStateAdapter', () => ({
  useChatStateAdapter: () => fixture.adapter,
}))

vi.mock('@/features/chat/controllers/useChatRouteSession', () => ({
  useChatRouteSession: () => ({
    sessionId: fixture.routeSessionId,
    router: { push: vi.fn(), replace: vi.fn() },
  }),
}))

vi.mock('next/navigation', async importOriginal => {
  const actual = await importOriginal<typeof import('next/navigation')>()
  return {
    ...actual,
    useSearchParams: () => new URLSearchParams(''),
  }
})

vi.mock('next/dynamic', () => ({
  // ChatWorkspace mounts pickers/modals off-screen via next/dynamic; a null
  // stub keeps their trees out of the smoke run without touching the shell.
  default: () => () => null,
}))

vi.mock('@/features/capabilities/useCapabilityCatalog', () => ({
  useCapabilityCatalog: () => ({
    capabilities: [
      {
        value: '',
        label: 'Chat',
        description: '',
        allowedTools: ['web_search', 'reason'],
        defaultTools: [],
      },
    ],
    visibleCapabilities: [{ value: '', label: 'Chat', description: '', allowedTools: [] }],
    isLoading: false,
  }),
}))

vi.mock('@/context/AppShellContext', () => ({
  useAppShell: () => ({
    setActiveSessionId: fixture.setActiveSessionId,
    language: 'en',
  }),
}))

vi.mock('@/hooks/useChatWorkspaces', () => ({
  useChatWorkspaces: () => ({ workspaces: [], error: '' }),
}))

vi.mock('@/hooks/useWorkspaceBinding', () => ({
  useWorkspaceBinding: () => ({
    selectWorkspace: vi.fn(),
    error: '',
    pending: false,
  }),
}))

vi.mock('@/hooks/useComposerResources', () => ({
  useComposerResources: () => ({ skills: [], mcp: [] }),
}))

vi.mock('@/hooks/useLLMOptions', () => ({
  useLLMOptions: () => ({
    options: [],
    activeDefault: null,
    loading: false,
    error: '',
    refresh: vi.fn(),
  }),
}))

vi.mock('@/hooks/useContextBudget', () => ({
  useContextBudget: () => null,
}))

vi.mock('@/lib/courses-api', () => ({
  listCourses: vi.fn(async () => []),
}))

vi.mock('@/lib/session-api', () => ({
  fetchSessionAskHint: vi.fn(async () => ''),
  updateSessionOrganization: vi.fn(async () => ({})),
  deleteSession: vi.fn(async () => {}),
  listSessionThreads: vi.fn(async () => []),
}))

vi.mock('@/features/knowledge/api/catalog', () => ({
  listKnowledgeBases: fixture.listKnowledgeBases,
}))

vi.mock('@/lib/subagents-api', () => ({
  getSubagentSettings: vi.fn(async () => ({ consult_budget: 3 })),
}))

vi.mock('@/lib/tools-settings', () => ({
  getEnabledOptionalTools: vi.fn(async () => []),
  invalidateEnabledOptionalToolsCache: vi.fn(),
}))

vi.mock('@/lib/notifications', () => ({
  notify: vi.fn(),
}))

vi.mock('@/lib/chat-export', () => ({
  downloadChatMarkdown: vi.fn(),
}))

vi.mock('@/components/chat/home/ChatComposer', () => ({
  default: (
    props: Record<string, unknown> & {
      isStreaming?: boolean
      onSend?: (content: string) => void
    }
  ) => {
    fixture.composerProps = props
    return (
      <div data-testid="composer" data-streaming={props.isStreaming ? 'true' : 'false'}>
        <button type="button" onClick={() => props.onSend?.('Explain photosynthesis')}>
          Send
        </button>
      </div>
    )
  },
}))

vi.mock('@/features/chat/messages', () => ({
  ChatMessageList: (props: {
    messages: MessageItem[]
    isStreaming: boolean
    canResendLastTurn: boolean
  }) => {
    fixture.listProps = props
    return (
      <div data-testid="message-list" data-streaming={props.isStreaming ? 'true' : 'false'}>
        {props.messages.map((message, index) => (
          <div key={message.id ?? index} data-role={message.role}>
            {message.content}
          </div>
        ))}
      </div>
    )
  },
}))

vi.mock('@/components/chat/home/StarterSuggestions', () => ({
  default: () => <div data-testid="starter-suggestions" />,
}))

vi.mock('@/components/chat/home/TurnNavigator', () => ({
  TurnNavigator: () => null,
}))

vi.mock('@/components/chat/preview/FilePreviewDrawer', () => ({
  default: () => null,
}))

vi.mock('@/components/watching/WatchingWorkspace', () => ({
  WatchingSessionBridge: () => null,
  WatchingSurface: () => null,
}))

vi.mock('@/components/watching/WatchingPane', () => ({
  WatchingPane: () => null,
  WATCHING_ASK_EVENT: 'dt:watching-ask',
}))

const { default: ChatWorkspace } = await import('@/features/chat/components/ChatWorkspace')

function emptyChatState(): ChatState {
  return {
    sessionKey: 'draft:general',
    sessionId: null,
    sessionTitle: '',
    enabledTools: [],
    activeCapability: '',
    workspaceMode: null,
    knowledgeBases: [],
    knowledgeBasesInitialized: true,
    llmSelection: null,
    masteryPathId: null,
    masterySessionMode: null,
    workspaceId: null,
    courseId: '',
    personaSelection: '',
    resourceSelection: { skills: [], mcp: [] },
    messages: [],
    isStreaming: false,
    currentStage: '',
    language: 'en',
    replyLanguageOverride: null,
    selectedBranches: {},
    lastTurnFailed: false,
    submissionFailed: false,
    submissionNoLlmAccess: false,
    submissionErrorMessage: '',
    submissionNeedsReview: false,
    submissionNotSaved: false,
    queuedMessages: [],
  }
}

function streamEvent(
  type: StreamEvent['type'],
  content: string,
  extra: Partial<StreamEvent> = {}
): StreamEvent {
  return {
    type,
    source: 'chat',
    stage: 'responding',
    content,
    timestamp: Date.now() / 1000,
    ...extra,
  } as StreamEvent
}

function renderWorkspace() {
  return render(<ChatWorkspace />)
}

/** Move the mocked adapter to its next state the way its provider would. */
function applyState(patch: Partial<ChatState>) {
  fixture.adapter.state = { ...fixture.adapter.state, ...patch }
}

describe('ChatWorkspace smoke', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    fixture.routeSessionId = null
    fixture.listKnowledgeBases.mockResolvedValue([])
    fixture.composerProps = null
    fixture.listProps = null
    fixture.adapter.state = emptyChatState()
  })

  it('initializes an untouched draft from its only ready default KB', async () => {
    applyState({ knowledgeBasesInitialized: false })
    fixture.listKnowledgeBases.mockResolvedValue([
      { id: 'workspace:kb:physics', name: 'physics', is_default: true, status: 'ready' },
    ])
    renderWorkspace()
    await waitFor(() => expect(fixture.adapter.initializeKBs).toHaveBeenCalledWith(
      'draft:general', null, ['workspace:kb:physics'],
    ))
  })

  it('does not replace an explicit empty selection', async () => {
    fixture.listKnowledgeBases.mockResolvedValue([
      { id: 'workspace:kb:physics', name: 'physics', is_default: true, status: 'ready' },
    ])
    renderWorkspace()
    await waitFor(() => expect(fixture.listKnowledgeBases).toHaveBeenCalled())
    expect(fixture.adapter.initializeKBs).not.toHaveBeenCalled()
  })

  it('does not invent a selection for multiple KBs', async () => {
    applyState({ knowledgeBasesInitialized: false })
    fixture.listKnowledgeBases.mockResolvedValue([
      { name: 'physics', is_default: true, status: 'ready' },
      { name: 'chemistry', is_default: false, status: 'ready' },
    ])
    renderWorkspace()
    await waitFor(() => expect(fixture.adapter.initializeKBs).toHaveBeenCalledWith(
      'draft:general', null, [],
    ))
  })

  it('does not select an unavailable default', async () => {
    applyState({ knowledgeBasesInitialized: false })
    fixture.listKnowledgeBases.mockResolvedValue([
      { name: 'physics', is_default: true, status: 'unavailable', available: false },
    ])
    renderWorkspace()
    await waitFor(() => expect(fixture.adapter.initializeKBs).toHaveBeenCalledWith(
      'draft:general', null, [],
    ))
  })

  it('does not send an unresolved untouched draft', async () => {
    applyState({ knowledgeBasesInitialized: false })
    fixture.listKnowledgeBases.mockImplementation(() => new Promise(() => {}))
    renderWorkspace()
    fireEvent.click(screen.getByText('Send'))
    await waitFor(() => expect(fixture.listKnowledgeBases).toHaveBeenCalled())
    expect(fixture.adapter.sendMessage).not.toHaveBeenCalled()
  })

  it('renders the empty draft home and bootstraps a new session', async () => {
    const view = renderWorkspace()

    // The draft session is created once on mount (no session id in the URL).
    await waitFor(() => expect(fixture.adapter.newSession).toHaveBeenCalledTimes(1))
    expect(fixture.adapter.newSession).toHaveBeenCalledWith({
      workspaceId: null,
    })

    // Empty home: greeting heading, starter suggestions, idle composer.
    const heading = screen.getByRole('heading', { level: 1 })
    expect(heading.textContent).toMatch(
      /What would you like to learn|Good morning|Good afternoon|Good evening|It's late|Morning —|Afternoon —|Evening —|Burning the midnight/
    )
    expect(screen.getByTestId('starter-suggestions')).toBeInTheDocument()
    expect(screen.getByTestId('composer')).toHaveAttribute('data-streaming', 'false')
    expect(screen.queryByTestId('message-list')).toBeNull()

    // Header affordances exist; export actions stay disabled with no turns.
    expect(screen.getByRole('button', { name: 'Download Markdown' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Save to Notebook' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Activity' })).toBeEnabled()

    // The active session is published to the app shell for the sidebar.
    expect(fixture.setActiveSessionId).toHaveBeenCalledWith(null)

    // A first send goes through the adapter's sendMessage.
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    })
    expect(fixture.adapter.sendMessage).toHaveBeenCalledWith(
      'Explain photosynthesis',
      [],
      expect.objectContaining({ _course_id: '' }),
      [],
      [],
      expect.anything(),
      [],
      undefined,
      []
    )
    view.unmount()
  })

  it('walks the streaming turn: sent → streaming → done', async () => {
    const view = renderWorkspace()
    await waitFor(() => expect(fixture.adapter.newSession).toHaveBeenCalled())

    // The adapter applies the optimistic user row once the turn starts.
    applyState({
      sessionId: 's-1',
      isStreaming: true,
      messages: [
        { id: 1, role: 'user', content: 'Explain photosynthesis' },
        { id: 2, role: 'assistant', content: '', events: [] },
      ],
    })
    view.rerender(<ChatWorkspace />)

    expect(screen.getByTestId('message-list')).toHaveAttribute('data-streaming', 'true')
    expect(
      within(screen.getByTestId('message-list')).getByText('Explain photosynthesis')
    ).toBeInTheDocument()
    expect(screen.getByTestId('composer')).toHaveAttribute('data-streaming', 'true')

    // Stream deltas land on the assistant row's event log.
    applyState({
      messages: [
        { id: 1, role: 'user', content: 'Explain photosynthesis' },
        {
          id: 2,
          role: 'assistant',
          content: 'Plants convert',
          events: [streamEvent('content', 'Plants convert')],
        },
      ],
    })
    view.rerender(<ChatWorkspace />)
    expect(
      within(screen.getByTestId('message-list')).getByText('Plants convert')
    ).toBeInTheDocument()
    expect(fixture.listProps?.messages[1].events).toHaveLength(1)

    // The turn settles: streaming flags drop on both surfaces.
    applyState({
      isStreaming: false,
      messages: [
        { id: 1, role: 'user', content: 'Explain photosynthesis' },
        {
          id: 2,
          role: 'assistant',
          content: 'Plants convert sunlight into sugar.',
          events: [
            streamEvent('content', 'Plants convert'),
            streamEvent('done', '', { metadata: { status: 'completed' } }),
          ],
        },
      ],
    })
    view.rerender(<ChatWorkspace />)
    expect(screen.getByTestId('message-list')).toHaveAttribute('data-streaming', 'false')
    expect(screen.getByTestId('composer')).toHaveAttribute('data-streaming', 'false')
    // No error banner while the turn is healthy.
    expect(screen.queryByRole('alert')).toBeNull()
    view.unmount()
  })

  it('queues a message sent mid-turn instead of dropping it', async () => {
    const view = renderWorkspace()
    await waitFor(() => expect(fixture.adapter.newSession).toHaveBeenCalled())
    applyState({
      sessionId: 's-1',
      isStreaming: true,
      messages: [
        { id: 1, role: 'user', content: 'Explain FFT' },
        { id: 2, role: 'assistant', content: '', events: [] },
      ],
    })
    view.rerender(<ChatWorkspace />)
    expect(fixture.composerProps?.queueWhileStreaming).toBe(true)

    fireEvent.click(screen.getByText('Send'))

    // Plain words can steer the running turn; nothing starts a second turn.
    await waitFor(() => expect(fixture.adapter.queueMessage).toHaveBeenCalledTimes(1))
    const [content, send, steerable] = fixture.adapter.queueMessage.mock.calls[0] as unknown as [
      string,
      unknown[],
      boolean,
    ]
    expect(content).toBe('Explain photosynthesis')
    expect(send[0]).toBe('Explain photosynthesis')
    expect(steerable).toBe(true)
    expect(fixture.adapter.sendMessage).not.toHaveBeenCalled()
    view.unmount()
  })

  it('shows the submission-failure banner and resends from it', async () => {
    const view = renderWorkspace()
    await waitFor(() => expect(fixture.adapter.newSession).toHaveBeenCalled())

    applyState({
      sessionId: 's-1',
      submissionFailed: true,
      lastTurnFailed: true,
      messages: [
        { id: 1, role: 'user', content: 'Explain photosynthesis' },
        {
          id: 2,
          role: 'user',
          content: 'Hello offline',
          failedSubmission: true,
        },
      ],
    })
    view.rerender(<ChatWorkspace />)

    const banner = screen.getByRole('alert')
    expect(banner).toHaveAttribute('data-submission-error', 'true')
    expect(banner).toHaveTextContent(
      "Couldn't reach the server. Please check your connection and retry."
    )
    // The unsent row renders as part of the transcript, marked by its flag.
    expect(fixture.listProps?.messages.at(-1)?.failedSubmission).toBe(true)

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Resend' }))
    })
    expect(fixture.adapter.resendLastMessage).toHaveBeenCalledTimes(1)

    applyState({ submissionNoLlmAccess: true })
    view.rerender(<ChatWorkspace />)
    expect(screen.getByRole('alert')).toHaveTextContent('Please contact your administrator')
    expect(screen.getByRole('button', { name: 'Resend' })).toBeEnabled()

    // The degraded variant (recovered text, lost context) explains itself
    // and offers no resend that would drop the missing context.
    applyState({ submissionNoLlmAccess: false, submissionNeedsReview: true })
    view.rerender(<ChatWorkspace />)
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Message text was saved, but its attachments or settings could not be restored. Copy it and send again.'
    )
    expect(screen.queryByRole('button', { name: 'Resend' })).toBeNull()
    view.unmount()
  })

  it('surfaces a retryable failure when the URL session cannot be loaded', async () => {
    fixture.routeSessionId = 's-404'
    fixture.adapter.loadSession.mockRejectedValue(new Error('gone'))
    const view = renderWorkspace()

    // Loading overlay first, then the terminal, retryable failure state.
    expect(await screen.findByRole('button', { name: 'Retry' })).toBeInTheDocument()
    expect(fixture.adapter.loadSession).toHaveBeenCalledWith(
      's-404',
      expect.objectContaining({ revalidate: false })
    )

    fixture.adapter.loadSession.mockResolvedValue([])
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    })
    await waitFor(() => expect(fixture.adapter.loadSession).toHaveBeenCalledTimes(2))
    view.unmount()
  })
})
