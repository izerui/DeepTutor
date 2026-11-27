import { beforeEach, expect, it, vi } from 'vitest'
import { browserStorage } from '@/shared/storage'
import {
  loadNoteDraft, noteDraftKeys, preserveNoteRecovery, readNoteRecoveries, removeNoteRecovery,
} from '@/features/notes/model/drafts'

const draft = {
  title: 'Unsaved', revision: 1,
  blocks: [{ id: 'a', type: 'paragraph', props: {}, content: 'Keep this' }],
}
beforeEach(() => localStorage.clear())

it('isolates both active and recovery drafts by account, workspace and document', () => {
  const keys = noteDraftKeys('alice', 'one', 'abcdef123456')
  localStorage.setItem(keys.draft, JSON.stringify(draft))
  preserveNoteRecovery(keys.recovery, draft)
  for (const other of [
    noteDraftKeys('bob', 'one', 'abcdef123456'),
    noteDraftKeys('alice', 'two', 'abcdef123456'),
    noteDraftKeys('alice', 'one', 'abcdef654321'),
  ]) expect(loadNoteDraft(other)).toEqual({ draft: null, recoveries: [] })
  expect(loadNoteDraft(keys)).toEqual({ draft, recoveries: [draft] })
})

it('keeps both legacy and scoped drafts when they differ', () => {
  const keys = noteDraftKeys('alice', 'one', 'abcdef123456')
  const newer = { ...draft, title: 'Newer', revision: 2 }
  localStorage.setItem(keys.legacy, JSON.stringify(draft))
  localStorage.setItem(keys.draft, JSON.stringify(newer))
  expect(loadNoteDraft(keys)).toEqual({ draft: newer, recoveries: [draft] })
  expect(localStorage.getItem(keys.legacy)).toBeNull()
})

it('retains every unresolved conflict until each is explicitly removed', () => {
  const keys = noteDraftKeys('alice', 'one', 'abcdef123456')
  const second = { ...draft, title: 'Another conflict' }
  preserveNoteRecovery(keys.recovery, draft)
  preserveNoteRecovery(keys.recovery, second)
  preserveNoteRecovery(keys.recovery, draft)
  expect(readNoteRecoveries(keys.recovery)).toEqual([draft, second])
  expect(removeNoteRecovery(keys.recovery, draft)).toEqual([second])
  expect(readNoteRecoveries(keys.recovery)).toEqual([second])
})

it('never deletes the legacy copy when browser storage refuses migration', () => {
  const keys = noteDraftKeys('alice', 'one', 'abcdef123456')
  localStorage.setItem(keys.legacy, JSON.stringify(draft))
  vi.spyOn(browserStorage, 'writeRaw').mockReturnValue(false)
  expect(() => loadNoteDraft(keys)).toThrow('Could not preserve')
  expect(localStorage.getItem(keys.legacy)).toBe(JSON.stringify(draft))
})
