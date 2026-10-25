'use client'

import { Suspense, useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'
import { useParams, useRouter, useSearchParams } from 'next/navigation'
import { ArrowLeft, Loader2, MessageSquare } from 'lucide-react'
import { notify } from '@/lib/notifications'
import { randomUuid } from '@/lib/random-uuid'
import { learningLibrary } from '@/lib/learning-library'
import { activeWorkspaceId } from '@/lib/workspace-scope'
import { useLearningCreation, requestedLearningCreation } from '@/components/learning/LibraryWorkspace'
import { bookRoute } from "@/lib/learning-routes";
import { useTranslation } from 'react-i18next'

import { BookApiError, bookApi, type BookWsEvent } from '@/lib/book-api'
import { bookErrorMessage } from '@/lib/book-errors'
import { bookRequestRef, bookRouteParam, bookSelectionKey, describeBookReferences } from '@/lib/book-sharing'
import { useAuthStatus } from '@/hooks/useAuthStatus'
import { Dialog } from '@/shared/ui/Dialog'
import BookShareDialog from './components/BookShareDialog'
import type {
  Block,
  BlockType,
  Book,
  BookDepth,
  BookDetail,
  BookProposal,
  Page,
  Spine,
  LearningCapture,
  BookReference,
} from '@/lib/book-types'
import { RESET_BOOK_PROGRESS, emptyBookProgress, reduceBookEvent } from '@/lib/book-progress'
import { bookEventKind, bookEventPageId, useBookStream } from '@/lib/use-book-stream'

import BookChatPanel from './components/BookChatPanel'
import BookCreator from './components/BookCreator'
import BookHealthBanner from './components/BookHealthBanner'
import BookLibrary from './components/BookLibrary'
import BookPausedBanner from './components/BookPausedBanner'
import BookGenerationActivity from './components/BookGenerationActivity'
import BookSidebar from './components/BookSidebar'
import PageReader from './components/PageReader'
import LearningCapturePanel from './components/LearningCapturePanel'
import SpineEditor from './components/SpineEditor'
import type { QuizAttemptArgs } from './components/blocks/QuizBlock'

/**
 * Which surface is on screen.
 *
 * `opening` is the one the route can assert before any data exists: a deep
 * book URL is not the library, and defaulting to `list` while the fetch was
 * in flight drew the (still empty) library first and jumped to the book when
 * it landed — a direct link or a refresh looked like a bad route (#1440).
 */
type View = 'list' | 'opening' | 'creator' | 'spine' | 'reader'

// Blocks land one at a time during compilation. Coalescing a burst into a
// single fetch keeps a page that is actively generating from issuing one
// request per block, while still feeling immediate to a reader watching it.
const PAGE_REFRESH_DEBOUNCE_MS = 250

// Book-level events arrive in bursts too — several stages finishing together,
// or the whole recent history replayed when a subscriber (re)connects. One
// burst should cost one refresh, not one per event.
const BOOK_REFRESH_DEBOUNCE_MS = 300

/** A page fetched as a summary has its blocks stripped; hydrate before reading. */
function needsHydration(page: Page | null): boolean {
  return !!page && page.blocks.length === 0 && (page.block_count ?? 0) > 0
}

/** Events that only change one chapter — refetch that page alone. */
const PAGE_SCOPED_EVENTS = new Set([
  'page_planned',
  'page_compile_started',
  'block_ready',
  'block_error',
  'page_compiled',
])

/** Events that change the book itself — chapter list, status, or both. */
const BOOK_SCOPED_EVENTS = new Set([
  'spine_ready',
  'overview_ready',
  'book_ready',
  'compilation_paused',
])

export default function BookPage() {
  // Keep one loading boundary for the library and its resource routes.
  return (
    <Suspense
      fallback={
        <div className="flex h-full w-full items-center justify-center text-[var(--muted-foreground)]">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" /> <BookLoadingText />
        </div>
      }
    >
      <BookSelection />
    </Suspense>
  )
}

function BookSelection() {
  const params = useParams<{ bookId?: string }>()
  const query = useSearchParams()
  const workspaceId = query.get('dt_workspace') ?? query.get('workspace') ?? ''
  // Every asynchronous reader operation belongs to this exact source, even
  // when another workspace/local import has the same bare model ID.
  return <BookPageInner key={bookSelectionKey(workspaceId, bookRouteParam(params.bookId))} workspaceId={workspaceId} />
}

function BookLoadingText() {
  const { t } = useTranslation()
  return <>{t('Loading…')}</>
}

function BookPageInner({ workspaceId }: { workspaceId: string }) {
  const { t } = useTranslation()
  const router = useRouter()
  const routeParams = useParams<{ bookId?: string; pageId?: string }>()
  const requestedBookId = bookRouteParam(routeParams.bookId)
  const requestedPageId = bookRouteParam(routeParams.pageId)
  const [books, setBooks] = useState<Book[]>([])
  const [canCreateBook, setCanCreateBook] = useState(true)
  const [loadingBooks, setLoadingBooks] = useState(true)
  const [booksError, setBooksError] = useState<string | null>(null)
  const { isAdmin } = useAuthStatus()
  const [sharingBook, setSharingBook] = useState<Book | null>(null)
  const [referencedDelete, setReferencedDelete] = useState<{ book: Book; references: BookReference[] } | null>(null)
  const [deleting, setDeleting] = useState(false)
  const alive = useRef(true)
  useEffect(() => {
    alive.current = true
    return () => { alive.current = false }
  }, [])
  const [view, setView] = useState<View>(requestedBookId ? 'opening' : requestedLearningCreation() ? 'creator' : 'list')

  const [selectedBookId, setSelectedBookId] = useState<string | null>(null)
  const selectionRequestRef = useRef(0)
  const [detail, setDetail] = useState<BookDetail | null>(null)
  const [selectedPageId, setSelectedPageId] = useState<string | null>(null)

  // Creator-stage state
  const [creating, setCreating] = useState(false)
  const [confirmingProposal, setConfirmingProposal] = useState(false)
  const [pendingProposal, setPendingProposal] = useState<BookProposal | null>(null)
  const [pendingBook, setPendingBook] = useState<Book | null>(null)

  // Spine-stage state
  const [confirmingSpine, setConfirmingSpine] = useState(false)

  // Page compile state
  const [compilingPageId, setCompilingPageId] = useState<string | null>(null)

  // Phase 3 state
  const [pendingDeepDiveTopic, setPendingDeepDiveTopic] = useState<string | null>(null)
  const [chatOpen, setChatOpen] = useState(false)
  const [rebuildingBook, setRebuildingBook] = useState(false)
  const [resumingBook, setResumingBook] = useState(false)
  const [pausingBook, setPausingBook] = useState(false)
  const [supplementingBlockId, setSupplementingBlockId] = useState<string | null>(null)
  const [regeneratingBlockId, setRegeneratingBlockId] = useState<string | null>(null)
  const regeneratingBlockIdRef = useRef<string | null>(null)
  const [learningCaptures, setLearningCaptures] = useState<LearningCapture[]>([])
  const [loadingLearningCaptures, setLoadingLearningCaptures] = useState(false)

  // Phase 5 — live BookEngine progress timeline state.
  const [progress, dispatchProgress] = useReducer(reduceBookEvent, null, emptyBookProgress)

  /**
   * The revision the server last confirmed, in a ref.
   *
   * The render's copy of `detail` is a snapshot, and two of these mutations
   * fire back to back inside one event handler: `handleConfirmSpine` claims a
   * revision and then immediately asks the engine to compile chapter one. No
   * state update can land between two statements, so that compile went out
   * quoting the revision from *before* the confirm and came back 409 —
   * "The book was updated by another collaborator", on a personal book, on
   * every single book, as the first thing generation ever said.
   */
  const revisionRef = useRef<number | undefined>(undefined)
  const pendingQuizSubmissions = useRef(new Map<string, { answer: string; id: string }>())

  // ── Data loaders ───────────────────────────────────────────────────

  /** Run a mutation, surfacing failures instead of dropping them. */
  const guard = useCallback(
    async (action: string, run: () => Promise<void>): Promise<void> => {
      if (!alive.current) return
      try {
        await run()
      } catch (err) {
        if (!alive.current) return
        if (err instanceof BookApiError && err.status === 409 && selectedBookId) {
          try {
            const latest = await bookApi.get(selectedBookId, {
              includeBlocks: false,
              workspaceId,
            })
            if (!alive.current) return
            revisionRef.current = latest.book.revision
            setDetail(latest)
          } catch {
            // Keep the original conflict as the actionable error.
          }
        }
        const reason = bookErrorMessage(err, t)
        notify(t('{{action}} failed: {{reason}}', { action, reason }), {
          tone: 'error',
          durationMs: 8000,
        })
        console.error(`${action} failed:`, err)
      }
    },
    [selectedBookId, workspaceId, t]
  )

  const refreshBooks = useCallback(async () => {
    if (!alive.current) return
    setLoadingBooks(true)
    try {
      const data = await learningLibrary<Book>('books')
      setBooks(data.items)
      setCanCreateBook(data.can_create)
      setBooksError(data.unavailable_workspaces.length ? t('Some workspaces could not be loaded. Available content is shown.') : null)
    } catch {
      setBooksError(t('Failed to load books'))
    } finally {
      setLoadingBooks(false)
    }
  }, [t])

  /**
   * Load a book without its block payloads.
   *
   * Only the page being read needs its blocks; fetching every page's rendered
   * content to draw a sidebar meant a multi-chapter book cost hundreds of
   * kilobytes per refresh. `hydratePage` fills in the one page that matters.
   */
  const loadBookDetail = useCallback(async (id: string, shouldApply: () => boolean = () => true) => {
    // A detached mutation must not refresh or start another operation. The
    // keyed reader protects state; this boundary also stops outgoing work.
    if (!alive.current) return null
    const data = await bookApi.get(id, { includeBlocks: false, workspaceId })
    if (!alive.current) return null
    if (shouldApply()) {
      revisionRef.current = data.book.revision
      setDetail(data)
    }
    return data
  }, [workspaceId])

  /** Replace one page in-place, leaving the rest of the book untouched. */
  const mergePage = useCallback((page: Page) => {
    if (!alive.current) return
    setDetail(current => {
      if (!current || current.book.id !== page.book_id) return current
      const index = current.pages.findIndex(p => p.id === page.id)
      if (index < 0) return current
      const pages = [...current.pages]
      pages[index] = page
      return { ...current, pages }
    })
  }, [])

  const applyBookRevision = useCallback((revision: number) => {
    if (!alive.current) return
    revisionRef.current = revision
    setDetail(current => (current ? { ...current, book: { ...current.book, revision } } : current))
  }, [])

  const hydratePage = useCallback(
    async (pageId: string) => {
      if (!selectedBookId || !alive.current) return
      try {
        const { page } = await bookApi.getPage(selectedBookId, pageId)
        mergePage(page)
      } catch (err) {
        console.error('hydratePage failed:', err)
      }
    },
    [selectedBookId, mergePage]
  )

  const refreshLearningCaptures = useCallback(
    async (bookId: string) => {
      if (!alive.current) return
      setLoadingLearningCaptures(true)
      try {
        const { captures } = await bookApi.listLearningCaptures(bookId)
        if (!alive.current) return
        setLearningCaptures(captures)
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err)
        notify(t('Could not load learning captures: {{message}}', { message: msg }), {
          tone: 'error',
          durationMs: 6000,
        })
        setLearningCaptures([])
      } finally {
        setLoadingLearningCaptures(false)
      }
    },
    [t]
  )

  // Debounced per page, so a burst of block events costs one fetch.
  const pageRefreshTimers = useRef(new Map<string, ReturnType<typeof setTimeout>>())
  const schedulePageRefresh = useCallback(
    (pageId: string) => {
      const timers = pageRefreshTimers.current
      const pending = timers.get(pageId)
      if (pending) clearTimeout(pending)
      timers.set(
        pageId,
        setTimeout(() => {
          timers.delete(pageId)
          void hydratePage(pageId)
        }, PAGE_REFRESH_DEBOUNCE_MS)
      )
    },
    [hydratePage]
  )

  const bookRefreshTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const scheduleBookRefresh = useCallback(() => {
    if (bookRefreshTimer.current) clearTimeout(bookRefreshTimer.current)
    bookRefreshTimer.current = setTimeout(() => {
      bookRefreshTimer.current = undefined
      if (selectedBookId) void loadBookDetail(selectedBookId)
      void refreshBooks()
    }, BOOK_REFRESH_DEBOUNCE_MS)
  }, [selectedBookId, loadBookDetail, refreshBooks])

  useEffect(() => {
    const timers = pageRefreshTimers.current
    return () => {
      timers.forEach(timer => clearTimeout(timer))
      timers.clear()
      if (bookRefreshTimer.current) clearTimeout(bookRefreshTimer.current)
    }
  }, [])

  useEffect(() => {
    void refreshBooks()
  }, [refreshBooks])

  // The generation timeline describes one book's run. Clear it when the reader
  // moves to another book, or the previous book's stages stay on screen.
  useEffect(() => {
    dispatchProgress(RESET_BOOK_PROGRESS)
  }, [selectedBookId])

  // ── Live event handling ────────────────────────────────────────────

  const handleBookEvent = useCallback(
    (event: BookWsEvent) => {
      dispatchProgress(event)

      const kind = bookEventKind(event)
      const pageId = bookEventPageId(event)

      // Page-scoped: refetch just that chapter. Refetching the whole book on
      // every block was the single most expensive thing this screen did.
      if (pageId && PAGE_SCOPED_EVENTS.has(kind)) {
        schedulePageRefresh(pageId)
        return
      }

      // Book-scoped: the chapter list or the book's own state moved.
      if (BOOK_SCOPED_EVENTS.has(kind)) {
        scheduleBookRefresh()
      }

      // Deliberately no toast here: `compilation_paused` is replayed to every
      // new subscriber, so a book that was paused once and resumed would pop a
      // stale alert on each reconnect. Pausing is durable state, and
      // BookPausedBanner renders it from `book.status`, which the refresh
      // above has just brought up to date.
    },
    [scheduleBookRefresh, schedulePageRefresh]
  )

  // One connection per open book, independent of any action in flight —
  // background compilation keeps streaming long after the call that queued it.
  useBookStream(selectedBookId, handleBookEvent)

  // Ideation runs before the book exists, so it has no stream of its own to
  // subscribe to; those events arrive on the creating socket instead.
  const handleCreationEvent = useCallback((event: BookWsEvent) => {
    dispatchProgress(event)
  }, [])

  // ── Selectors ──────────────────────────────────────────────────────

  const selectedPage: Page | null = useMemo(() => {
    if (!detail || !selectedPageId) return null
    return detail.pages.find(p => p.id === selectedPageId) || null
  }, [detail, selectedPageId])

  // Reading order is the page order — the sidebar shows the same sequence.
  const pageNeighbours = useMemo(() => {
    if (!detail || !selectedPageId) return { previous: null, next: null }
    const index = detail.pages.findIndex(p => p.id === selectedPageId)
    if (index < 0) return { previous: null, next: null }
    return {
      previous: detail.pages[index - 1] || null,
      next: detail.pages[index + 1] || null,
    }
  }, [detail, selectedPageId])

  const selectedPageChatSessionId = useMemo(() => {
    if (!detail?.book || !selectedPage) return null
    const sessions = detail.book.metadata?.page_chat_sessions
    return sessions?.[selectedPage.id] || null
  }, [detail?.book, selectedPage])

  const canEditBook = detail?.book.can_edit !== false
  const expectedRevision = detail?.book.revision
  /** The freshest revision we know of — see `revisionRef`. */
  const currentRevision = useCallback(
    () => revisionRef.current ?? detail?.book.revision,
    [detail]
  )

  // ── Handlers ───────────────────────────────────────────────────────

  const handleNewBook = () => {
    if (!canCreateBook) return
    setSelectedBookId(null)
    setDetail(null)
    setPendingBook(null)
    setPendingProposal(null)
    setSelectedPageId(null)
    setView('creator')
  }

  const creation = useLearningCreation(handleNewBook)

  // Defined after handleSelectBook below.
  const lastDeepLinkedBookId = useRef<string | null>(null)

  const handleSelectBook = useCallback(
    async (id: string | null, openPageId?: string | null) => {
      if (!alive.current) return
      const requestId = ++selectionRequestRef.current
      if (!id) {
        setSelectedBookId(null)
        setDetail(null)
        setView('list')
        if (requestedBookId) router.push(bookRoute())
        return
      }
      const targetPath = bookRoute(id, openPageId)
      if (requestedBookId !== id) {
        router.push(targetPath)
        return
      }
      setSelectedBookId(id)
      const data = await loadBookDetail(id, () => selectionRequestRef.current === requestId)
      if (!data || !alive.current || selectionRequestRef.current !== requestId) return
      const hasReadableContent = data.pages.some(
        p => p.status !== 'pending' || (p.block_count ?? p.blocks.length) > 0
      )
      const canEdit = data.book.can_edit !== false
      if (canEdit && data.book.status === 'draft' && data.book.proposal) {
        setPendingBook(data.book)
        setPendingProposal(data.book.proposal)
        setView('creator')
      } else if (
        canEdit &&
        data.book.status === 'spine_ready' &&
        data.spine &&
        !hasReadableContent
      ) {
        // Spine confirmed but nothing built yet — the editor is still the right
        // place. Once any chapter exists, the reader is.
        setView('spine')
      } else {
        // Resume where the reader left off — that's what `current_page_id` is
        // for, and until now nothing ever read it.
        const requested = (openPageId && data.pages.find(p => p.id === openPageId)) || null
        const resumed = data.pages.find(p => p.id === data.progress.current_page_id) || null
        const firstReady = data.pages.find(p => p.status === 'ready') || null
        const target = requested || resumed || firstReady || data.pages[0] || null
        setSelectedPageId(target?.id || null)
        setView('reader')
      }
    },
    [loadBookDetail, requestedBookId, router]
  )

  // Resource identity belongs in the path: /books/<book>[/pages/<page>].
  useEffect(() => {
    if (!requestedBookId) {
      lastDeepLinkedBookId.current = null
      if (selectedBookId) void handleSelectBook(null)
      return
    }
    if (requestedBookId === selectedBookId) return
    if (requestedBookId === lastDeepLinkedBookId.current) return
    lastDeepLinkedBookId.current = requestedBookId
    // The shell is now drawn before the book exists, so a load that fails has
    // to say so and hand the reader back; otherwise the shell is where they
    // stay. Selecting nothing returns to the library and to its URL.
    void handleSelectBook(requestedBookId, requestedPageId).catch(err => {
      if (!alive.current) return
      if (lastDeepLinkedBookId.current !== requestedBookId) return
      notify(
        t('{{action}} failed: {{reason}}', {
          action: t('Open book'),
          reason: bookErrorMessage(err, t),
        }),
        { tone: 'error', durationMs: 8000 }
      )
      console.error('Open book failed:', err)
      void handleSelectBook(null)
    })
  }, [requestedBookId, requestedPageId, selectedBookId, handleSelectBook, t])

  useEffect(() => {
    if (view !== 'reader' || !selectedBookId) {
      if (!selectedBookId) setLearningCaptures([])
      return
    }
    void refreshLearningCaptures(selectedBookId)
  }, [view, selectedBookId, refreshLearningCaptures])

  const deleteBook = async (book: Book, force = false) =>
    guard('Delete book', async () => {
      const id = bookRequestRef(book)
      await bookApi.delete(id, book.content_workspace_id ?? '', { force })
      if (!alive.current) return
      if (selectedBookId === id) {
        setSelectedBookId(null)
        setDetail(null)
        setView('list')
        router.replace(bookRoute())
      }
      await refreshBooks()
    })

  const handleDeleteBook = async (book: Book) => guard('Delete book', async () => {
    // Empty ref is an unregistered local collision. Its management endpoints
    // intentionally reject 409; DELETE still authorizes that local location.
    if (book.ref) {
      const { references } = await bookApi.references(book.ref, book.content_workspace_id ?? '')
      if (!alive.current) return
      if (references.length) {
        setReferencedDelete({ book, references })
        return
      }
    }
    await deleteBook(book)
  })

  const handleResumeBook = async () => {
    if (!alive.current) return
    if (!detail || !canEditBook) return
    setResumingBook(true)
    try {
      await bookApi.resume(selectedBookId!, currentRevision())
      await loadBookDetail(selectedBookId!)
      await refreshBooks()
    } catch (err) {
      if (!alive.current) return
      const msg = bookErrorMessage(err, t)
      notify(t('Could not resume: {{message}}', { message: msg }), {
        tone: 'error',
        durationMs: 8000,
      })
      if (err instanceof BookApiError && err.status === 409) {
        await loadBookDetail(selectedBookId!)
      }
    } finally {
      setResumingBook(false)
    }
  }

  const handlePauseBook = async () => {
    if (!alive.current) return
    if (!detail || !canEditBook || detail.book.status !== 'compiling') return
    setPausingBook(true)
    try {
      const result = await bookApi.pause(selectedBookId!, currentRevision())
      applyBookRevision(result.book_revision)
      await loadBookDetail(selectedBookId!)
      await refreshBooks()
    } catch (err) {
      if (!alive.current) return
      const msg = bookErrorMessage(err, t)
      notify(t('Could not pause: {{message}}', { message: msg }), {
        tone: 'error',
        durationMs: 8000,
      })
      if (err instanceof BookApiError && err.status === 409) {
        await loadBookDetail(selectedBookId!)
      }
    } finally {
      setPausingBook(false)
    }
  }

  const handleRebuildBook = async () =>
    guard('Rebuild book', async () => {
      if (!detail || !canEditBook) return
      setRebuildingBook(true)
      try {
        await bookApi.rebuild(selectedBookId!, true, currentRevision())
        const refreshed = await loadBookDetail(selectedBookId!)
        if (!refreshed) return
        setSelectedPageId(refreshed.pages[0]?.id || null)
        setView('reader')
        await refreshBooks()
      } finally {
        setRebuildingBook(false)
      }
    })

  const handleCreate = async (payload: {
    user_intent: string
    chat_session_id: string
    chat_selections: Array<{ session_id: string; message_ids: number[] }>
    knowledge_bases: string[]
    notebook_refs: Array<Record<string, unknown>>
    question_categories: number[]
    question_entries: number[]
    language: string
    fallback_language: string
    depth: BookDepth
  }) => {
    if (!alive.current) return
    setCreating(true)
    try {
      const result = await bookApi.create(payload, handleCreationEvent)
      if (!alive.current) return
      setPendingBook(result.book)
      setPendingProposal(result.proposal)
      router.push(bookRoute(bookRequestRef(result.book)))
      await refreshBooks()
    } finally {
      setCreating(false)
    }
  }

  const handleConfirmProposal = async (edited: BookProposal) => {
    if (!alive.current) return
    if (!pendingBook || pendingBook.can_edit === false) return
    setConfirmingProposal(true)
    try {
      // No per-action event callback: the book now has a stream of its own,
      // and `useBookStream` is already listening. Passing one too would feed
      // the timeline every event twice.
      const result = await bookApi.confirmProposal(selectedBookId || bookRequestRef(pendingBook), edited, pendingBook.revision)
      if (!alive.current) return
      setPendingBook(result.book)
      setPendingProposal(null)
      if (!await loadBookDetail(selectedBookId || bookRequestRef(result.book))) return
      setView('spine')
      await refreshBooks()
    } finally {
      setConfirmingProposal(false)
    }
  }

  const handleConfirmSpine = async (
    spine: Spine,
    autoCompile: boolean,
    blockTypes: string[] | null
  ) => {
    if (!alive.current) return
    if (!detail || !canEditBook) return
    setConfirmingSpine(true)
    try {
      const confirmed = await bookApi.confirmSpine(
        selectedBookId!,
        spine,
        autoCompile,
        currentRevision(),
        blockTypes ?? undefined
      )
      if (!alive.current) return
      // Claim the token this call just advanced before anything else runs.
      // The refresh below would also pick it up, but the compile that starts
      // in the same handler must not be able to read the old one.
      applyBookRevision(confirmed.book_revision)
      const refreshed = await loadBookDetail(selectedBookId!)
      if (!refreshed) return
      const firstPage = refreshed.pages[0] || null
      setSelectedPageId(firstPage?.id || null)
      setView('reader')
      if (firstPage && canEditBook) {
        void compilePage(firstPage.id)
      }
      await refreshBooks()
    } catch (err) {
      if (!alive.current) return
      const msg = bookErrorMessage(err, t)
      notify(t('Confirm spine failed: {{message}}', { message: msg }), {
        tone: 'error',
        durationMs: 8000,
      })
      if (err instanceof BookApiError && err.status === 409) {
        await loadBookDetail(selectedBookId!)
      }
    } finally {
      setConfirmingSpine(false)
    }
  }

  const compilePage = useCallback(
    async (pageId: string, force = false) => {
      if (!alive.current) return
      if (!selectedBookId || !canEditBook) return
      setCompilingPageId(pageId)
      try {
        // Progress arrives on the book's stream; this call just awaits the
        // finished page. The engine coalesces it with any run already in
        // flight, so opening a page the worker reached first is free.
        const run = async () => {
          if (!alive.current) return
          const { page, book_revision } = await bookApi.compilePage(
            selectedBookId,
            pageId,
            force,
            currentRevision()
          )
          applyBookRevision(book_revision)
          mergePage(page)
        }
        try {
          await run()
        } catch (err) {
          if (!alive.current) return
          // A stale token is not a failed compile, and telling the reader
          // "generation failed" for one is how the whole feature came to look
          // broken. Catch up on the book and ask again, once.
          if (!(err instanceof BookApiError) || err.status !== 409) throw err
          await loadBookDetail(selectedBookId)
          await run()
        }
      } catch (err) {
        if (!alive.current) return
        const msg = bookErrorMessage(err, t)
        notify(t('Compile failed: {{message}}', { message: msg }), {
          tone: 'error',
          durationMs: 8000,
        })
        console.error('compilePage failed:', err)
        void loadBookDetail(selectedBookId)
      } finally {
        setCompilingPageId(current => (current === pageId ? null : current))
      }
    },
    [selectedBookId, canEditBook, currentRevision, applyBookRevision, mergePage, loadBookDetail, t]
  )

  const handleSelectPage = useCallback(
    (pageId: string) => {
      if (!alive.current) return
      setSelectedPageId(pageId)
      if (selectedBookId && requestedPageId !== pageId) {
        router.push(bookRoute(selectedBookId, pageId))
      }
      if (!detail) return

      const page = detail.pages.find(p => p.id === pageId)
      if (!page) return
      // Hydration is handled by the reader effect below, which fires for
      // every route into a page — including the one that opens the book.
      if (
        canEditBook &&
        detail.book.status !== 'paused' &&
        page.status !== 'ready' &&
        page.status !== 'generating'
      ) {
        void compilePage(pageId)
      }
    },
    [detail, canEditBook, compilePage, requestedPageId, router, selectedBookId]
  )

  // A ?page= change on a book that is already open just moves the reader.
  const lastDeepLinkedPageId = useRef<string | null>(null)
  useEffect(() => {
    if (!requestedPageId || !requestedBookId) return
    if (requestedBookId !== selectedBookId) return
    const routeKey = `${requestedBookId}:${requestedPageId}`
    if (routeKey === lastDeepLinkedPageId.current) return
    lastDeepLinkedPageId.current = routeKey
    if (requestedPageId !== selectedPageId) handleSelectPage(requestedPageId)
  }, [requestedPageId, requestedBookId, selectedBookId, selectedPageId, handleSelectPage])

  // Opening a book lands on a page without going through `handleSelectPage`,
  // so hydrate and record the visit here too. The ref keeps recording a visit
  // from re-triggering itself when the updated progress comes back.
  const recordedVisitRef = useRef<string | null>(null)
  useEffect(() => {
    if (view !== 'reader' || !selectedBookId || !selectedPage) return
    if (needsHydration(selectedPage)) void hydratePage(selectedPage.id)

    const key = `${selectedBookId}:${selectedPage.id}`
    if (recordedVisitRef.current === key) return
    recordedVisitRef.current = key
    const bookId = selectedBookId
    void bookApi
      .markVisited(bookId, selectedPage.id)
      .then(({ progress }) =>
        setDetail(current =>
          alive.current && current ? { ...current, progress } : current
        )
      )
      .catch(() => {
        // Position tracking is best-effort.
      })
  }, [view, selectedBookId, selectedPage, hydratePage])

  const handleUpdateBody = async (block: Block, body: string) => {
    if (!alive.current) return
    if (!detail || !selectedPage) return
    const pageId = selectedPage.id
    try {
      const { book_revision } = await bookApi.updateBlock({
        book_id: selectedBookId!,
        page_id: pageId,
        block_id: block.id,
        body,
        expected_revision: currentRevision(),
      })
      applyBookRevision(book_revision)
      await hydratePage(pageId)
    } catch (err) {
      if (!alive.current) return
      const msg = err instanceof Error ? err.message : String(err)
      notify(t('Could not save your edit: {{message}}', { message: msg }), {
        tone: 'error',
        durationMs: 8000,
      })
      if (err instanceof BookApiError && err.status === 409) {
        await loadBookDetail(selectedBookId!)
      }
      throw err
    }
  }

  const handleToggleBookmark = async () =>
    guard('Bookmark', async () => {
      if (!detail || !selectedPage) return
      const bookId = selectedBookId!
      const { progress } = await bookApi.toggleBookmark(bookId, selectedPage.id)
      setDetail(current =>
        alive.current && current ? { ...current, progress } : current
      )
    })

  const handleRegenerateBlock = async (block: Block) => {
    if (!alive.current) return
    if (!detail || !selectedPage || regeneratingBlockIdRef.current) return
    const bookId = selectedBookId!
    const isSharedBook = detail.book.source === 'shared'
    const pageId = selectedPage.id
    regeneratingBlockIdRef.current = block.id
    setRegeneratingBlockId(block.id)
    try {
      const regenerate = (expectedRevision: number | undefined) =>
        bookApi.regenerateBlock(bookId, pageId, block.id, undefined, expectedRevision)

      const result = await regenerate(currentRevision()).catch(err => {
        // Background chapter generation also advances the book revision. For
        // a personal book there is no collaborator to overwrite, so catch up
        // to the server token and transparently retry the requested action.
        // Shared books keep the conflict visible to protect another editor's
        // newly-written content.
        if (
          !alive.current ||
          !(err instanceof BookApiError) ||
          err.code !== 'book_revision_conflict' ||
          isSharedBook ||
          err.currentRevision === undefined
        ) {
          return Promise.reject(err)
        }
        applyBookRevision(err.currentRevision)
        return regenerate(err.currentRevision)
      })

      const { book_revision } = result
      applyBookRevision(book_revision)
    } catch (err) {
      if (!alive.current) return
      const msg = bookErrorMessage(err, t)
      notify(t('Regenerate block failed: {{message}}', { message: msg }), {
        tone: 'error',
        durationMs: 8000,
      })
      if (err instanceof BookApiError && err.status === 409) {
        await loadBookDetail(bookId)
      }
      console.error('regenerateBlock failed:', err)
    } finally {
      await hydratePage(pageId)
      regeneratingBlockIdRef.current = null
      setRegeneratingBlockId(null)
    }
  }

  const handleDeleteBlock = async (block: Block) =>
    guard('Delete block', async () => {
      if (!detail || !selectedPage) return
      const { book_revision } = await bookApi.deleteBlock(
        selectedBookId!,
        selectedPage.id,
        block.id,
        currentRevision()
      )
      applyBookRevision(book_revision)
      await hydratePage(selectedPage.id)
    })

  const handleMoveBlock = async (block: Block, direction: 'up' | 'down') =>
    guard('Move block', async () => {
      if (!detail || !selectedPage) return
      const idx = selectedPage.blocks.findIndex(b => b.id === block.id)
      if (idx < 0) return
      const newPos = direction === 'up' ? idx - 1 : idx + 1
      if (newPos < 0 || newPos >= selectedPage.blocks.length) return
      const { book_revision } = await bookApi.moveBlock(
        selectedBookId!,
        selectedPage.id,
        block.id,
        newPos,
        currentRevision()
      )
      applyBookRevision(book_revision)
      await hydratePage(selectedPage.id)
    })

  const handleChangeBlockType = async (block: Block, newType: BlockType) =>
    guard('Change block type', async () => {
      if (!detail || !selectedPage) return
      const { book_revision } = await bookApi.changeBlockType({
        book_id: selectedBookId!,
        page_id: selectedPage.id,
        block_id: block.id,
        new_type: newType,
        expected_revision: currentRevision(),
      })
      applyBookRevision(book_revision)
      await hydratePage(selectedPage.id)
    })

  const handleInsertBlock = async (block_type: BlockType) =>
    guard('Insert block', async () => {
      if (!detail || !selectedPage) return
      const { book_revision } = await bookApi.insertBlock({
        book_id: selectedBookId!,
        page_id: selectedPage.id,
        block_type,
        expected_revision: currentRevision(),
      })
      applyBookRevision(book_revision)
      await hydratePage(selectedPage.id)
    })

  const handleDeepDive = async (topic: string, blockId: string) =>
    guard('Deep dive', async () => {
      if (!detail || !selectedPage) return
      setPendingDeepDiveTopic(topic)
      try {
        const result = await bookApi.deepDive({
          book_id: selectedBookId!,
          parent_page_id: selectedPage.id,
          topic,
          block_id: blockId,
          expected_revision: currentRevision(),
        })
        applyBookRevision(result.book_revision)
        // A deep dive adds a page, so the chapter list itself changed.
        const refreshed = await loadBookDetail(selectedBookId!)
        if (!refreshed) return
        const newPage = refreshed.pages.find(p => p.id === result.page.id)
        if (newPage) {
          setSelectedPageId(newPage.id)
          mergePage(result.page)
        }
      } finally {
        setPendingDeepDiveTopic(null)
      }
    })

  const handleQuizAttempt = async (block: Block, args: QuizAttemptArgs) =>
    guard('Record answer', async () => {
      if (!detail || !selectedPage) return
      const bookId = selectedBookId!
      const key = `${bookId}:${selectedPage.id}:${block.id}:${args.questionId || ''}`
      const answer = `${args.userAnswer || ''}:${String(args.isCorrect)}`
      const pending = pendingQuizSubmissions.current.get(key)
      const submissionId = pending?.answer === answer ? pending.id : randomUuid()
      pendingQuizSubmissions.current.set(key, { answer, id: submissionId })
      const { progress } = await bookApi.recordQuizAttempt({
        book_id: bookId,
        page_id: selectedPage.id,
        block_id: block.id,
        question_id: args.questionId,
        user_answer: args.userAnswer,
        is_correct: args.isCorrect,
        submission_id: submissionId,
      })
      if (pendingQuizSubmissions.current.get(key)?.id === submissionId) {
        pendingQuizSubmissions.current.delete(key)
        setDetail(current =>
          alive.current && current ? { ...current, progress } : current
        )
      }
    })

  /**
   * Add a remediation callout + explanation + easier quiz for a topic.
   *
   * Explicitly requested, never automatic. This grows the page by three
   * generated blocks, and having that happen unannounced under the reader —
   * once per wrong click, with nothing stopping a second round — was both
   * startling and expensive.
   */
  const handleRequestSupplement = async (block: Block) => {
    if (!alive.current) return
    if (!detail || !selectedPage) return
    const pageId = selectedPage.id
    const topic = (block.params?.topic as string | undefined) || selectedPage.title || ''
    setSupplementingBlockId(block.id)
    try {
      const { book_revision } = await bookApi.supplement(
        selectedBookId!,
        pageId,
        topic,
        currentRevision()
      )
      applyBookRevision(book_revision)
      await hydratePage(pageId)
    } catch (err) {
      if (!alive.current) return
      const msg = err instanceof Error ? err.message : String(err)
      notify(t('Could not add extra practice: {{message}}', { message: msg }), {
        tone: 'error',
        durationMs: 8000,
      })
      if (err instanceof BookApiError && err.status === 409 && detail) {
        await loadBookDetail(selectedBookId!)
      }
    } finally {
      setSupplementingBlockId(null)
    }
  }

  const refreshCapturesSorted = useCallback((captures: LearningCapture[]) => {
    return [...captures].sort((a, b) => b.updated_at - a.updated_at)
  }, [])

  const applyCapturePatch = useCallback(
    (capture: LearningCapture) => {
      if (!alive.current) return
      setLearningCaptures(current =>
        refreshCapturesSorted(
          current.some(item => item.id === capture.id)
            ? current.map(item => (item.id === capture.id ? capture : item))
            : [capture, ...current]
        )
      )
    },
    [refreshCapturesSorted]
  )

  const handleCaptureSelection = async (payload: {
    page_id: string
    block_id: string
    source_text: string
    context_before?: string
    context_after?: string
    source_locator?: string
  }) => {
    if (!alive.current) return
    if (!detail) return
    const result = await bookApi.createLearningCapture(selectedBookId!, {
      ...payload,
      book_title: detail.book.title,
      chapter_title: selectedPage?.title || '',
    })
    applyCapturePatch(result.capture)
  }

  const handleApproveCapture = async (capture: LearningCapture) => {
    if (!alive.current) return
    if (!detail) return
    const result = await bookApi.updateLearningCapture(selectedBookId!, capture.id, {
      status: 'approved',
    })
    applyCapturePatch(result.capture)
    notify(t('Capture approved'), {
      tone: 'success',
      durationMs: 3000,
    })
  }

  const handleRejectCapture = async (capture: LearningCapture) => {
    if (!alive.current) return
    if (!detail) return
    const result = await bookApi.updateLearningCapture(selectedBookId!, capture.id, {
      status: 'rejected',
    })
    applyCapturePatch(result.capture)
    notify(t('Capture rejected'), {
      tone: 'info',
      durationMs: 3000,
    })
  }

  const handlePageChatSession = async (sessionId: string) =>
    guard('Link chat session', async () => {
      if (!detail || !selectedPage || !sessionId) return
      const existing = detail.book.metadata?.page_chat_sessions?.[selectedPage.id]
      if (existing === sessionId) return
      const result = await bookApi.setPageChatSession(selectedBookId!, selectedPage.id, sessionId)
      setDetail(current =>
        alive.current && current && current.book.id === result.book.id ? { ...current, book: result.book } : current
      )
    })

  // ── Render ─────────────────────────────────────────────────────────

  // A client-side route change may arrive before the next book's detail.
  // Keep the previous reader, sidebar, and activity out of the new URL.
  const displayView = requestedBookId && (selectedBookId !== requestedBookId || !detail)
    ? 'opening' : view

  return (
    <div className="flex h-full w-full">
      {displayView !== 'list' && displayView !== 'creator' && displayView !== 'opening' && (
        <BookSidebar
          book={detail?.book || pendingBook || null}
          requestedRef={selectedBookId ?? undefined}
          onBackToLibrary={() => void handleSelectBook(null)}
          pages={detail?.pages || []}
          selectedPageId={selectedPageId}
          onSelectPage={handleSelectPage}
          onRebuild={detail && canEditBook ? () => void handleRebuildBook() : undefined}
          rebuilding={rebuildingBook}
          visitedPageIds={detail?.progress.visited_page_ids}
          bookmarkedPageIds={detail?.progress.bookmarked_page_ids}
        />
      )}

      <main className="relative flex flex-1 overflow-hidden bg-[var(--background)]">
        {/* One slot, one place, every view. The predecessor put a gradient
            progress card *inside* the creator's scrolling content and a
            second chip floating over the corner, so starting generation
            shoved the page down, finishing it snapped the page back, and the
            two readouts disagreed about what to show. Fixed slot above the
            view: it changes height only when the reader asks it to. */}
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          {displayView !== 'list' && displayView !== 'opening' && (
            <BookGenerationActivity
              book={detail?.book || pendingBook || null}
              pages={detail?.pages || []}
              spine={detail?.spine}
              generation={detail?.generation}
              progress={progress}
              onOpenPage={detail ? handleSelectPage : undefined}
              onPause={
                canEditBook && detail?.book.status === 'compiling'
                  ? () => void handlePauseBook()
                  : undefined
              }
              onResume={canEditBook && detail ? () => void handleResumeBook() : undefined}
              pausing={pausingBook}
              resuming={resumingBook}
            />
          )}
          <div className="min-h-0 flex-1 overflow-hidden">
          {displayView === 'opening' && (
            <div role="status" aria-busy="true" className="flex h-full w-full items-center justify-center gap-2 text-[12px] text-[var(--muted-foreground)]">
              <Loader2 className="h-4 w-4 animate-spin" />
              <BookLoadingText />
            </div>
          )}

          {creation.dialog}
          {displayView === 'list' && (
            <BookLibrary
              books={books}
              loading={loadingBooks}
              error={booksError}
              onRetry={() => void refreshBooks()}
              canCreate={canCreateBook}
              onNewBook={creation.begin}
              onSelectBook={(id, workspaceId) => {
                if (workspaceId !== activeWorkspaceId()) router.push(bookRoute(id, null, workspaceId))
                else void handleSelectBook(id)
              }}
              onDeleteBook={book => void handleDeleteBook(book)}
              onShareBook={setSharingBook}
            />
          )}
          <BookShareDialog book={sharingBook} isAdmin={isAdmin}
            onClose={() => setSharingBook(null)} onChanged={() => void refreshBooks()} />
          <Dialog open={!!referencedDelete} alert title={t('Delete book')} closeLabel={t('Close')}
            onClose={() => setReferencedDelete(null)} busy={deleting}
            footer={<>
              <button type="button" data-autofocus disabled={deleting} onClick={() => setReferencedDelete(null)}
                className="rounded-lg px-3 py-1.5 text-sm">{t('Cancel')}</button>
              {isAdmin && <button type="button" disabled={deleting} className="rounded-lg bg-red-600 px-3 py-1.5 text-sm text-white"
                onClick={() => {
                  if (!referencedDelete || deleting) return
                  setDeleting(true)
                  void deleteBook(referencedDelete.book, true).finally(() => {
                    if (!alive.current) return
                    setDeleting(false)
                    setReferencedDelete(null)
                  })
                }}>{t('Delete book')}</button>}
            </>}>
            {referencedDelete && <p className="break-words">{t(isAdmin
              ? 'This book is still referenced by: {{items}}. Delete it anyway? Those items will lose this book.'
              : 'This book is still referenced by: {{items}}. Remove those references before deleting it.',
            { items: describeBookReferences(referencedDelete.references, t) })}</p>}
          </Dialog>

          {displayView === 'creator' && (
            <div className="h-full overflow-y-auto [scrollbar-gutter:stable]">
              <BookCreator
                back={<button type="button" onClick={() => void handleSelectBook(null)} className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"><ArrowLeft size={14} />{t('All books')}</button>}
                book={pendingBook}
                onCreate={handleCreate}
                loading={creating}
                proposal={pendingProposal}
                onConfirmProposal={handleConfirmProposal}
                confirmLoading={confirmingProposal}
              />
            </div>
          )}

          {displayView === 'spine' && detail?.spine && (
            <div className="flex h-full flex-col overflow-hidden">
              <div className="flex-1 overflow-hidden">
                <SpineEditor
                  key={`${detail.spine.book_id}:${detail.spine.version}`}
                  spine={detail.spine}
                  onConfirm={handleConfirmSpine}
                  loading={confirmingSpine}
                  depth={detail.book.depth}
                  initialBlockTypes={
                    Array.isArray(detail.book.metadata?.block_types)
                      ? (detail.book.metadata.block_types as string[])
                      : undefined
                  }
                />
              </div>
            </div>
          )}

          {displayView === 'reader' && (
            // Column layout so banners push the reader down instead of
            // overflowing it — `PageReader` fills whatever height is left.
            <div className="flex h-full flex-col overflow-hidden">
              <BookPausedBanner
                book={detail?.book || null}
                onResume={canEditBook ? () => void handleResumeBook() : undefined}
                resuming={resumingBook}
              />
              <BookHealthBanner
                bookId={selectedBookId}
                refreshKey={detail?.book.updated_at}
                expectedRevision={expectedRevision}
                onRevisionChange={applyBookRevision}
                explorationFailed={!!detail?.book.metadata?.exploration_failed}
                onRecompile={
                  canEditBook
                    ? pageId => {
                        setSelectedPageId(pageId)
                        void compilePage(pageId, true)
                      }
                    : undefined
                }
              />
              {detail?.book.source === 'shared' && (
                <div className="mx-6 mt-3 rounded-lg border border-sky-300/60 bg-sky-50 px-3 py-2 text-xs text-sky-900 dark:border-sky-500/30 dark:bg-sky-500/10 dark:text-sky-100">
                  {canEditBook
                    ? t(
                        'Shared book: content edits affect everyone who can access it. Reading progress and captures remain private to you.'
                      )
                    : t(
                        'Shared book (read only): your reading progress, bookmarks and captures remain private to you.'
                      )}
                </div>
              )}
              {/* The reader owns the height that is left; the capture inbox
                  sits under it at its natural height. Both need this to be a
                  flex column — a plain block here collapses `PageReader`'s
                  `h-full` to auto, which stops the body scrolling and pushes
                  the page-turn footer out of view. */}
              <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
                <div className="min-h-0 flex-1 overflow-hidden">
                  <PageReader
                    page={selectedPage}
                    bookId={selectedBookId ?? undefined}
                    bookLanguage={detail?.book.language}
                    loading={!!compilingPageId && compilingPageId === selectedPage?.id}
                    onRegenerateBlock={
                      canEditBook ? block => handleRegenerateBlock(block) : undefined
                    }
                    onDeleteBlock={canEditBook ? block => void handleDeleteBlock(block) : undefined}
                    onMoveBlock={canEditBook ? (block, dir) => void handleMoveBlock(block, dir) : undefined}
                    onChangeBlockType={canEditBook ? (block, type) => void handleChangeBlockType(block, type) : undefined}
                    onInsertBlock={canEditBook ? t => handleInsertBlock(t) : undefined}
                    onDeepDive={canEditBook ? (topic, blockId) => handleDeepDive(topic, blockId) : undefined}
                    onOpenPage={pageId => handleSelectPage(pageId)}
                    onQuizAttempt={(block, args) => void handleQuizAttempt(block, args)}
                    onRequestSupplement={
                      canEditBook ? block => void handleRequestSupplement(block) : undefined
                    }
                    supplementingBlockId={supplementingBlockId}
                    regeneratingBlockId={regeneratingBlockId}
                    onUpdateBody={canEditBook ? handleUpdateBody : undefined}
                    attempts={detail?.progress.quiz_attempts}
                    previousPage={pageNeighbours.previous}
                    nextPage={pageNeighbours.next}
                    onNavigate={handleSelectPage}
                    bookmarked={
                      !!selectedPage &&
                      !!detail?.progress.bookmarked_page_ids.includes(selectedPage.id)
                    }
                    onToggleBookmark={() => void handleToggleBookmark()}
                    pendingDeepDiveTopic={pendingDeepDiveTopic}
                    onRecompile={
                      canEditBook && selectedPage
                        ? () => void compilePage(selectedPage.id, true)
                        : undefined
                    }
                    onCaptureSelection={payload =>
                      void guard('Save selection', () => handleCaptureSelection(payload))
                    }
                  />
                </div>
                {/* The wrapper goes with the panel: a bordered strip with
                    nothing in it is the same occupied space by another name. */}
                {learningCaptures.length > 0 && (
                <div className="shrink-0 border-t border-[var(--border)] px-8 py-3">
                  <LearningCapturePanel
                    captures={learningCaptures}
                    loading={loadingLearningCaptures}
                    onApprove={capture =>
                      void guard('Approve capture', () => handleApproveCapture(capture))
                    }
                    onReject={capture =>
                      void guard('Reject capture', () => handleRejectCapture(capture))
                    }
                  />
                </div>
                )}
              </div>
            </div>
          )}

          {displayView === 'spine' && !detail?.spine && (
            <div className="flex h-full items-center justify-center text-[var(--muted-foreground)]">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" /> {t('Loading spine…')}
            </div>
          )}
          </div>
        </div>

        {displayView === 'reader' && !chatOpen && (
          <button
            onClick={() => setChatOpen(true)}
            className="absolute bottom-4 right-4 inline-flex items-center gap-2 rounded-full bg-[var(--primary)] px-4 py-2 text-sm font-medium text-[var(--primary-foreground)] shadow-lg hover:opacity-90"
          >
            <MessageSquare className="h-4 w-4" />
            {t('Chat')}
          </button>
        )}

        {displayView === 'reader' && chatOpen && (
          <BookChatPanel
            key={selectedPageId}
            book={detail?.book || null}
            requestedRef={selectedBookId ?? undefined}
            page={selectedPage}
            open={chatOpen}
            onClose={() => setChatOpen(false)}
            initialSessionId={selectedPageChatSessionId}
            onSessionResolved={sessionId => void handlePageChatSession(sessionId)}
          />
        )}
      </main>
    </div>
  )
}
