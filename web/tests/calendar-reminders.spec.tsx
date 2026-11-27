import { act, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import CalendarRuntime from '@/features/calendar/components/CalendarRuntime'
import { notify } from '@/lib/notifications'
import { listOccurrences } from '@/features/calendar/transport/api'

let userId = 'alice'
const t = (key: string) => key
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t, i18n: { language: 'en' } }) }))
vi.mock('@/hooks/useAuthStatus', () => ({
  useAuthStatus: () => ({
    userId, enabled: true, authenticated: true, loading: false, statusAvailable: true,
  }),
}))
vi.mock('@/lib/notifications', () => ({ notify: vi.fn() }))
vi.mock('@/features/calendar/transport/api', () => ({
  listOccurrences: vi.fn(),
  calendarStreamUrl: () => '/api/calendar/stream',
}))

function response() {
  const start = new Date(Date.now() + 60_000).toISOString()
  return {
    revision: 1,
    occurrences: [{
      id: 'shared-id', title: 'Reminder', start, reminders: [1],
      reminder_times: [new Date(Date.now() - 1000).toISOString()], all_day: false,
    }],
  } as Awaited<ReturnType<typeof listOccurrences>>
}
beforeEach(() => {
  userId = 'alice'
  localStorage.clear()
  vi.stubGlobal('EventSource', undefined)
  vi.mocked(listOccurrences).mockResolvedValue(response())
})
afterEach(() => vi.unstubAllGlobals())

it('deduplicates reminders per account without suppressing the next account', async () => {
  const view = render(<CalendarRuntime>{null}</CalendarRuntime>)
  await waitFor(() => expect(notify).toHaveBeenCalledTimes(1))
  await act(async () => window.dispatchEvent(new Event('focus')))
  expect(notify).toHaveBeenCalledTimes(1)
  userId = 'bob'
  view.rerender(<CalendarRuntime>{null}</CalendarRuntime>)
  await waitFor(() => expect(notify).toHaveBeenCalledTimes(2))
  expect(localStorage.getItem('deeptutor.calendar.reminders:alice')).toContain('shared-id')
  expect(localStorage.getItem('deeptutor.calendar.reminders:bob')).toContain('shared-id')
})

it('discards the previous account reminder request when it resolves after switching accounts', async () => {
  const pending = Promise.withResolvers<Awaited<ReturnType<typeof listOccurrences>>>()
  vi.mocked(listOccurrences).mockReturnValueOnce(pending.promise)
  const view = render(<CalendarRuntime>{null}</CalendarRuntime>)
  userId = 'bob'
  vi.mocked(listOccurrences).mockResolvedValue({ occurrences: [], revision: 0 })
  view.rerender(<CalendarRuntime>{null}</CalendarRuntime>)
  await act(async () => pending.resolve(response()))
  expect(notify).not.toHaveBeenCalled()
  expect(localStorage.getItem('deeptutor.calendar.reminders:alice')).toBeNull()
  expect(localStorage.getItem('deeptutor.calendar.reminders:bob')).toBeNull()
})
