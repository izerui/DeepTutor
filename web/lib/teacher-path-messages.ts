/**
 * What a learner is told when a teacher path refuses something, in their own
 * language. The server's answers are stable English constants written for
 * logs; the code (or the HTTP status on a teacher path) is the contract, so
 * the wording lives here and is looked up by it.
 */

/** Stable codes of a refused mastery turn (`metadata.error_code`). */
export const TEACHER_PATH_UNAVAILABLE_CODE = 'teacher_path_unavailable'
export const TEACHER_MATERIALS_UNAVAILABLE_CODE = 'teacher_materials_unavailable'

/** `QuizAttempt.void_reason` of answers to an objective a teacher redefined. */
export const OBJECTIVE_REDEFINED_REASON = 'objective_redefined'

export const COURSE_UNAVAILABLE_KEY =
  'This course is no longer available to you. Ask your teacher if you think this is a mistake.'
export const MATERIALS_UNAVAILABLE_KEY = 'Course materials are temporarily unavailable'
export const MATERIALS_UNAVAILABLE_DETAIL_KEY =
  'Ask your teacher to restore them. The tutor will not continue without them.'

/** A catalog key plus its interpolation values; callers render `t(key, values)`. */
export interface TeacherPathMessage {
  key: string
  values?: Record<string, string>
}

function materialNames(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value
    .map(item => {
      if (typeof item === 'string') return item
      if (item && typeof item === 'object') {
        const row = item as Record<string, unknown>
        return String(row.name ?? row.label ?? row.title ?? '')
      }
      return ''
    })
    .map(name => name.trim())
    .filter(Boolean)
}

/**
 * The localized message for a turn a teacher path refused, or null when the
 * event is some other failure (its own content is shown as before).
 */
export function teacherTurnErrorMessage(
  metadata: Record<string, unknown> | undefined
): TeacherPathMessage | null {
  const code = String(metadata?.error_code ?? metadata?.code ?? '')
  if (code === TEACHER_PATH_UNAVAILABLE_CODE) return { key: COURSE_UNAVAILABLE_KEY }
  if (code === TEACHER_MATERIALS_UNAVAILABLE_CODE) {
    const names = materialNames(metadata?.materials)
    return names.length
      ? {
          key: 'Course materials are temporarily unavailable: {{materials}}. Ask your teacher to restore them. The tutor will not continue without them.',
          values: { materials: names.join(' · ') },
        }
      : {
          key: 'Course materials are temporarily unavailable. Ask your teacher to restore them. The tutor will not continue without them.',
        }
  }
  return null
}

/** Why an attempt no longer counts, as a short label beside it. */
export function voidedAttemptLabel(reason: string | undefined): string {
  return reason === OBJECTIVE_REDEFINED_REASON ? 'Previous objective' : 'Not counted'
}
