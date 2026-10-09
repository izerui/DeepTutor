/** Content scope belongs to the current task URL, never to a global tab preference. */
export function activeWorkspaceId(): string {
  if (typeof window === 'undefined') return ''
  const query = new URLSearchParams(window.location.search)
  return query.get('dt_workspace') ?? query.get('workspace') ?? ''
}

const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? ''

/** Extract the pathname portion before any '?' or '#'. */
function pathnamePart(path: string): string {
  const q = path.indexOf('?')
  const h = path.indexOf('#')
  const end = q >= 0 && h >= 0 ? Math.min(q, h) : q >= 0 ? q : h >= 0 ? h : path.length
  return path.slice(0, end)
}

function hasBasePath(pathname: string): boolean {
  return pathname === BASE_PATH || pathname.startsWith(BASE_PATH + '/')
}

/** Prepend basePath to a public asset path (e.g. "/logo.png" → "/kaoyan/logo.png"). */
export function assetPath(path: string): string {
  if (!BASE_PATH || !path.startsWith('/') || path.startsWith('//')) return path
  if (hasBasePath(pathnamePart(path))) return path
  return BASE_PATH + path
}

/**
 * Prepend basePath for paths that go directly to the browser (fetch, WebSocket,
 * window.location). Handles both relative paths ("/api/...") and absolute
 * same-origin URLs ("http://localhost:3000/api/...").
 * Next.js router.push/replace auto-prepends basePath, so navigation paths
 * must NOT use this.
 */
export function browserPath(path: string): string {
  if (!BASE_PATH) return path
  if (path.startsWith('//')) return path
  if (path.startsWith('/')) {
    if (hasBasePath(pathnamePart(path))) return path
    return BASE_PATH + path
  }
  if (/^https?:\/\//.test(path)) {
    try {
      const url = new URL(path)
      const origin = typeof window !== 'undefined' ? window.location.origin : ''
      if (origin && url.origin === origin && !hasBasePath(url.pathname)) {
        url.pathname = BASE_PATH + url.pathname
        return url.toString()
      }
    } catch { /* not a parseable URL */ }
  }
  return path
}

/** Strip basePath from a browser-captured path so it can be used for Next.js navigation. */
export function stripBasePath(path: string): string {
  if (!BASE_PATH) return path
  const pn = pathnamePart(path)
  if (pn === BASE_PATH) return '/' + path.slice(BASE_PATH.length)
  if (pn.startsWith(BASE_PATH + '/')) return path.slice(BASE_PATH.length)
  return path
}

export function scopedUrl(path: string, workspaceId = activeWorkspaceId()): string {
  const origin = typeof window === 'undefined' ? 'http://workspace.local' : window.location.origin
  if (path.startsWith('//')) return path
  const absolute = /^https?:\/\//.test(path)
  if (!path.startsWith('/') && !absolute) return path
  const url = new URL(path, origin)
  if (url.origin !== origin) return path
  if (url.pathname === '/kanban' || url.pathname === '/api/task-board' || url.pathname.startsWith('/api/task-board/')) {
    url.searchParams.delete('dt_workspace')
    url.searchParams.delete('workspace')
    return absolute ? url.toString() : `${url.pathname}${url.search}${url.hash}`
  }
  if (!url.searchParams.has('dt_workspace'))
    url.searchParams.set('dt_workspace', url.searchParams.get('workspace') ?? workspaceId)
  return absolute ? url.toString() : `${url.pathname}${url.search}${url.hash}`
}

let navigate: ((path: string) => void) | undefined
let navigationVersion = 0

/** Installed by the root navigation bridge; callers need no router singleton. */
export function registerWorkspaceNavigator(push: (path: string) => void): () => void {
  navigate = push
  return () => { if (navigate === push) navigate = undefined }
}

export async function selectWorkspace(
  workspaceId: string,
  destination = '/learning',
  options?: { beforeNavigate?: () => Promise<void> }
): Promise<void> {
  const version = ++navigationVersion
  const pending: Promise<void>[] = []
  window.dispatchEvent(new CustomEvent('deeptutor:before-workspace-switch', { detail: pending }))
  try {
    await Promise.all(pending)
    if (version !== navigationVersion) return
    await options?.beforeNavigate?.()
  } catch {
    const { notify } = await import('./notifications')
    const { default: i18n } = await import('i18next')
    notify(i18n.t('Could not save the draft. Free browser storage before switching workspaces.'), { tone: 'error' })
    window.dispatchEvent(new CustomEvent('deeptutor:workspace-switch-error'))
    return
  }
  if (version !== navigationVersion) return
  // Next owns navigation. The workspace layout resets only scoped runtimes;
  // the document, account settings and loaded assets stay in place.
  const url = new URL(scopedUrl(destination, workspaceId), window.location.origin)
  navigate?.(`${url.pathname}${url.search}${url.hash}`)
}

/** Keep live runtimes when staying in one store; reset them before crossing stores. */
export function navigateTask(path: string, push: (path: string) => void): void {
  const url = new URL(path, window.location.origin)
  const target = url.searchParams.get('dt_workspace') ?? url.searchParams.get('workspace') ?? ''
  if (target === activeWorkspaceId()) push(path)
  else void selectWorkspace(target, path)
}
