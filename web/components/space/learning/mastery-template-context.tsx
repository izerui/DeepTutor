'use client'

import { createContext, useContext } from 'react'

/**
 * The teacher path a Study page is showing, or "". Source previews inside the
 * transcript add it to their knowledge-base requests so a reference-only
 * library opens for exactly those reads (the server checks the assignment).
 * Only an open path provides it: a closed one would turn every read into 403,
 * including originals the learner could read without it.
 */
export const MasteryTemplateContext = createContext<string>('')

export function useMasteryTemplate(): string {
  return useContext(MasteryTemplateContext)
}
