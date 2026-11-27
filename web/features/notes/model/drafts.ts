import { browserStorage } from '@/shared/storage'
import type { NoteBlock } from '@/lib/notes-api'

export interface NoteDraft {
  title: string
  blocks: NoteBlock[]
  revision?: number
}

export function noteDraftKeys(owner: string, workspace: string, docId: string) {
  const scope = [owner, workspace, docId].map(encodeURIComponent).join(':')
  return {
    draft: `deeptutor.notes.draft.v2:${scope}`,
    recovery: `deeptutor.notes.recovery.v2:${scope}`,
    legacy: `deeptutor.notes.draft:${owner}:${docId}`,
  }
}

export function parseNoteDraft(raw: string | null): NoteDraft | null {
  try {
    const value = JSON.parse(raw || 'null')
    return value && typeof value.title === 'string' && Array.isArray(value.blocks) ? value : null
  } catch {
    return null
  }
}

export function readNoteRecoveries(key: string): NoteDraft[] {
  try {
    const values = JSON.parse(browserStorage.readRaw('local', key) || '[]')
    return Array.isArray(values)
      ? values.flatMap(value => parseNoteDraft(JSON.stringify(value)) || [])
      : []
  } catch {
    return []
  }
}

export function preserveNoteRecovery(key: string, draft: NoteDraft): NoteDraft[] {
  const drafts = readNoteRecoveries(key)
  if (!drafts.some(item => JSON.stringify(item) === JSON.stringify(draft))) drafts.push(draft)
  if (!browserStorage.writeRaw('local', key, JSON.stringify(drafts)))
    throw new Error('Could not preserve the local draft. Free browser storage and retry.')
  return drafts
}

export function removeNoteRecovery(key: string, draft: NoteDraft): NoteDraft[] {
  const drafts = readNoteRecoveries(key).filter(item => JSON.stringify(item) !== JSON.stringify(draft))
  const saved = drafts.length
    ? browserStorage.writeRaw('local', key, JSON.stringify(drafts))
    : browserStorage.removeRaw('local', key)
  if (!saved) throw new Error('Could not update local drafts. Free browser storage and retry.')
  return drafts
}

/** Call only after the server has authorized this account/workspace to read the page. */
export function loadNoteDraft(keys: ReturnType<typeof noteDraftKeys>) {
  let draft = parseNoteDraft(browserStorage.readRaw('local', keys.draft))
  const legacy = parseNoteDraft(browserStorage.readRaw('local', keys.legacy))
  if (legacy) {
    if (draft && JSON.stringify(draft) !== JSON.stringify(legacy)) {
      preserveNoteRecovery(keys.recovery, legacy)
    } else if (!draft) {
      if (!browserStorage.writeRaw('local', keys.draft, JSON.stringify(legacy)))
        throw new Error('Could not preserve the local draft. Free browser storage and retry.')
      draft = legacy
    }
    browserStorage.removeRaw('local', keys.legacy)
  }
  return { draft, recoveries: readNoteRecoveries(keys.recovery) }
}
