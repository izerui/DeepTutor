import { LEARNING_HUB } from '@/lib/learning-routes'
import {
  Bot,
  CalendarDays,
  ClipboardCheck,
  ClipboardList,
  GraduationCap,
  HeartHandshake,
  House,
  LayoutGrid,
  ListTodo,
  MessagesSquare,
  PenLine,
  PenSquare,
  Settings,
  Store,
  type LucideIcon,
} from 'lucide-react'

import type { Capability } from '@/lib/capability-routes'

export interface NavEntry {
  href: string
  label: string
  icon: LucideIcon
  tooltipKey?: string
  defaultCollapsed?: boolean
  /** Model capability this feature needs; locked when the user lacks it. */
  requires?: Capability
  /**
   * Learning-account surfaces that may see this entry. Absent means the
   * entry is hidden whenever a learning policy is in effect (#1222).
   */
  surfaces?: readonly string[]
  /** Batch 3 §10: shown only to these roles (absent: everyone). */
  roles?: readonly NavRole[]
  /** Batch 3 §7: for a student, shown only when this ``student_policy`` key allows it. */
  studentFeature?: string
}

export type NavRole = 'admin' | 'teacher' | 'student'

/** Whether a role (and a student's permissions) sees an entry. */
export function navVisibleForRole(
  href: string,
  role: NavRole | null,
  studentPolicy: Record<string, boolean> | null,
): boolean {
  const entry = NAV_BY_HREF.get(href)
  if (!entry || role === null) return true
  if (entry.roles && !entry.roles.includes(role)) return false
  if (role === 'student' && entry.studentFeature) return Boolean(studentPolicy?.[entry.studentFeature])
  return true
}

/**
 * The workspace features, in the order they ship in.
 *
 * This is the *default* arrangement, not the rendered one — a learner can
 * reorder these and fold the ones they don't use into "More"
 * (``lib/sidebar-layout.ts``). Adding an entry here places it for everyone,
 * including people who have already arranged their sidebar: it arrives next to
 * the neighbour it follows below rather than at the bottom of their list.
 */
export const PRIMARY_NAV: NavEntry[] = [
  // Spec 2026-11-03 batch 1 §2: what is assigned, what the school published, what is one's own.
  { href: '/start', label: 'Home', icon: House, tooltipKey: 'Home page tooltip', surfaces: ['chat'] },
  // Platform batch 1 §5.1: the learning market, for everyone.
  { href: '/market', label: 'Learning market', icon: Store, tooltipKey: 'Learning market tooltip', surfaces: ['chat'] },
  {
    href: '/chat',
    label: 'Chat',
    icon: MessagesSquare,
    tooltipKey: 'Home tooltip',
    requires: 'llm',
    surfaces: ['chat'],
  },
  {
    href: '/partners',
    label: 'Partners',
    icon: HeartHandshake,
    tooltipKey: 'Partners tooltip',
    requires: 'llm',
    studentFeature: 'allow_partners',
  },
  {
    href: LEARNING_HUB,
    label: 'Personalized Learning',
    icon: GraduationCap,
    tooltipKey: 'One tutor, your own way to learn.',
    surfaces: ['reading', 'chat', 'books'],
  },
  { href: '/space', label: 'Learning Space', icon: LayoutGrid, tooltipKey: 'Space tooltip' },
  // Platform batch 2 §3: classes are retired; the /classes page only explains that.
  // Spec 2026-10-29 §8: teachers write practice sets; students answer them.
  {
    href: '/practice-sets',
    label: 'Practice sets',
    icon: ClipboardList,
    tooltipKey: 'Practice sets tooltip',
    surfaces: ['chat'],
    // Batch 3 §10: students reach their practice sets through tasks.
    roles: ['admin', 'teacher'],
  },
  // Spec 2026-11-03 batch 1: tasks and homework.
  // Batch 3 §5.4: every hand-in waiting for the teacher.
  { href: '/grading', label: 'Grading', icon: PenSquare, tooltipKey: 'Grading tooltip', surfaces: ['chat'], roles: ['teacher'] },
  { href: '/tasks', label: 'Tasks', icon: ClipboardCheck, tooltipKey: 'Tasks tooltip', surfaces: ['chat'] },
  // Spec 2026-11-03 batch 2 §7: openings and deadlines by month.
  { href: '/calendar', label: 'Calendar', icon: CalendarDays, tooltipKey: 'Calendar tooltip', surfaces: ['chat'] },
  {
    href: '/kanban',
    label: 'Task Board',
    icon: ListTodo,
    tooltipKey: 'kanban.intro',
    surfaces: ['chat'],
  },
  {
    href: '/co-writer',
    label: 'Co-Writer',
    icon: PenLine,
    tooltipKey: 'Co-Writer tooltip',
    requires: 'llm',
    defaultCollapsed: true,
    studentFeature: 'allow_co_writer',
  },
  {
    href: '/agents',
    label: 'My Agents',
    icon: Bot,
    tooltipKey: 'Agents tooltip',
    defaultCollapsed: true,
    studentFeature: 'allow_agents',
  },
]

export const SECONDARY_NAV: NavEntry[] = [{ href: '/settings', label: 'Settings', icon: Settings }]

export const DEFAULT_COLLAPSED_NAV = PRIMARY_NAV.filter(entry => entry.defaultCollapsed).map(
  entry => entry.href
)

export const PRIMARY_NAV_HREFS = PRIMARY_NAV.map(entry => entry.href)

export const NAV_BY_HREF = new Map(
  [...PRIMARY_NAV, ...SECONDARY_NAV].map(entry => [entry.href, entry])
)

export function isNavActive(pathname: string, href: string) {
  return pathname === href || pathname.startsWith(`${href}/`)
}

/**
 * A learning policy hides every feature the account cannot call. Settings
 * stays, because the redacted page (theme, language) is still theirs.
 * ``allowedSurfaces === null`` means no policy: the full sidebar.
 */
export function navVisibleUnderPolicy(
  href: string,
  allowedSurfaces: readonly string[] | null,
): boolean {
  if (allowedSurfaces === null) return true
  if (href === '/settings') return true
  const surfaces = NAV_BY_HREF.get(href)?.surfaces
  if (!surfaces?.length) return false
  return surfaces.some(surface => allowedSurfaces.includes(surface))
}
