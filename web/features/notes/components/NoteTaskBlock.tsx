'use client'
import Link from 'next/link'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  getTaskBoard,
  createTaskCard,
  updateTaskCard,
  subscribeTaskBoardChanges,
  type TaskCard,
} from '@/lib/task-board-api'
import { useActiveWorkspaceId } from '@/hooks/useActiveWorkspaceId'
export default function NoteTaskBlock({
  data,
  prompt,
  onChange,
}: {
  data: string
  prompt: string
  onChange: (props: { data?: string }) => void
}) {
  const { t } = useTranslation()
  const workspaceId = useActiveWorkspaceId()
  const [card, setCard] = useState<TaskCard | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  let taskId = ''
  try {
    taskId = JSON.parse(data).taskId || ''
  } catch {
    /* Unlinked task. */
  }
  useEffect(() => {
    let cancelled = false
    setCard(null)
    setError('')
    void getTaskBoard(undefined, workspaceId || '')
      .then(board => {
        if (!cancelled) setCard(board.cards.find(c => c.id === taskId) || null)
      })
      .catch(e => {
        if (!cancelled) setError(String(e))
      })
    const unsubscribe = subscribeTaskBoardChanges((board, scope) => {
      if (scope === (workspaceId || '')) setCard(board.cards.find(c => c.id === taskId) || null)
    }, workspaceId || '')
    return () => {
      cancelled = true
      unsubscribe()
    }
  }, [taskId, workspaceId])
  const create = async () => {
    setBusy(true)
    try {
      const before = await getTaskBoard(undefined, workspaceId || '')
      const previous = new Set(before.cards.map(c => c.id))
      const after = await createTaskCard(prompt || t('New task'), {
        workspace_id: workspaceId || '',
      })
      const created = after.cards.find(
        c => !previous.has(c.id) && c.title === (prompt || t('New task'))
      )
      if (created) {
        setCard(created)
        onChange({ data: JSON.stringify({ taskId: created.id }) })
      }
    } catch (err) {
      setError(String(err))
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      {card ? (
        <>
          <input
            type="checkbox"
            aria-label={t('Complete task')}
            checked={card.status === 'done'}
            onChange={e =>
              void updateTaskCard(card.id, { status: e.target.checked ? 'done' : 'todo' }).catch(
                e => setError(String(e))
              )
            }
          />
          <span>{card.title}</span>
          <input
            type="date"
            aria-label={t('Due date')}
            value={card.due_date || ''}
            className="bg-transparent text-xs"
            onChange={e =>
              void updateTaskCard(card.id, { due_date: e.target.value || null }).catch(e =>
                setError(String(e))
              )
            }
          />
          <Link href="/kanban" className="text-xs text-[var(--primary)]">
            {t('Open task board')}
          </Link>
        </>
      ) : (
        <button
          disabled={busy || !!taskId}
          className="dt-note-action"
          onClick={() => void create()}
        >
          {t(taskId ? 'Task unavailable' : 'Create linked task')}
        </button>
      )}
      {error && <p role="alert">{error}</p>}
    </div>
  )
}
