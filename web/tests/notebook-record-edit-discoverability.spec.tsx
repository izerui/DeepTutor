import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import NotebookRecordRow from '@/components/notebook/NotebookRecordRow'
import type { NotebookRecordItem } from '@/lib/notebook-api'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('@/components/common/MarkdownRenderer', () => ({ default: () => null }))

const record: NotebookRecordItem = {
  id: 'record-1',
  type: 'chat',
  title: 'Stored record',
  user_query: 'What changed?',
  output: 'The record body.',
}

function renderRow(onEdit = vi.fn().mockResolvedValue(undefined)) {
  function Harness() {
    const [expanded, setExpanded] = useState(false)

    return (
      <NotebookRecordRow
        record={record}
        notebooks={[]}
        currentNotebookId="notebook-1"
        expanded={expanded}
        onToggle={() => setExpanded(value => !value)}
        onEdit={onEdit}
        onDelete={vi.fn().mockResolvedValue(undefined)}
        onRelocate={vi.fn().mockResolvedValue(undefined)}
        onOpenSession={vi.fn()}
      />
    )
  }

  render(<Harness />)
}

it('opens the record editor directly without opening the row menu', async () => {
  const onEdit = vi.fn().mockResolvedValue(undefined)
  renderRow(onEdit)

  const editButton = screen.getByRole('button', { name: 'Edit record' })
  expect(editButton).toBeVisible()
  expect(screen.queryByRole('menu')).not.toBeInTheDocument()

  fireEvent.click(editButton)
  const titleInput = screen.getByLabelText('Record title')
  expect(titleInput).toHaveValue('Stored record')

  fireEvent.change(titleInput, { target: { value: 'Updated record' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))

  await waitFor(() => expect(onEdit).toHaveBeenCalledWith('record-1', { title: 'Updated record' }))
})

it('keeps the saved original read-only while saving title and summary changes', async () => {
  const onEdit = vi.fn().mockResolvedValue(undefined)
  renderRow(onEdit)
  fireEvent.click(screen.getByRole('button', { name: 'Edit record' }))
  const body = screen.getByRole('textbox', { name: 'Body' })
  expect(body).toHaveAttribute('readonly')
  await userEvent.setup().type(body, 'Do not overwrite the original')
  expect(body).toHaveValue(record.output)
  expect(screen.queryByRole('tab', { name: 'Write' })).toBeNull()
  expect(await screen.findByRole('button', { name: 'Add to a note' })).toBeVisible()
  fireEvent.change(screen.getByLabelText('Record title'), { target: { value: 'New title' } })
  fireEvent.change(screen.getByLabelText('Short summary'), { target: { value: 'My summary' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save' }))
  await waitFor(() =>
    expect(onEdit).toHaveBeenCalledWith('record-1', { title: 'New title', summary: 'My summary' })
  )
})
