'use client'

import { useSearchParams } from 'next/navigation'

/** Content scope follows navigation, including query-only workspace switches. */
export function useActiveWorkspaceId(): string {
  const query = useSearchParams()
  return query.get('dt_workspace') ?? query.get('workspace') ?? ''
}
