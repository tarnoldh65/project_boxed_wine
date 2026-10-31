import { fireEvent, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import type { Case } from '../api/client'
import { json, mockApi, renderWithQuery } from '../testing'
import { CASE, MEMBERS } from './fixtures'
import { TasksPanel } from './TasksPanel'

afterEach(() => vi.unstubAllGlobals())

const TASKS = '/api/tenants/dr/cases/7/tasks'

function render(current: Case = CASE, editable = true) {
  renderWithQuery(<TasksPanel slug="dr" current={current} members={MEMBERS} editable={editable} />)
}

function api() {
  return mockApi({
    [`PATCH ${TASKS}/t1`]: async (request) => json({ ...CASE, ...(await request.json()) }),
    [`POST ${TASKS}`]: () => json(CASE, 201),
  })
}

async function lastBody(requests: Request[]) {
  await vi.waitFor(() => expect(requests).not.toHaveLength(0))
  return requests.at(-1)!.json()
}

test('shows tasks with dependencies, blocking, due dates, and checklists', () => {
  render()
  expect(screen.getByText('Identify recipients', { selector: 'strong' })).toBeInTheDocument()
  expect(screen.getByText('After: Identify recipients')).toBeInTheDocument()
  expect(screen.getAllByText('blocked')).toHaveLength(1)
  expect(screen.getByLabelText('Query mail logs')).not.toBeChecked()
})

test('changes status, assignee, and checklist items', async () => {
  const requests = api()
  render()
  fireEvent.change(screen.getByLabelText('Status of Identify recipients'), { target: { value: 'done' } })
  expect(await lastBody(requests)).toEqual({ status: 'done' })
  fireEvent.change(screen.getByLabelText('Assignee of Identify recipients'), { target: { value: 'u2' } })
  expect(await lastBody(requests)).toEqual({ assignee_id: 'u2' })
  fireEvent.click(screen.getByLabelText('Query mail logs'))
  expect(await lastBody(requests)).toEqual({ checklist: [{ text: 'Query mail logs', done: true }] })
})

test('waives a task with a reason and reopens it', async () => {
  const requests = api()
  render()
  fireEvent.click(screen.getByRole('button', { name: 'Waive Identify recipients' }))
  fireEvent.change(screen.getByLabelText('Reason to waive Identify recipients'), {
    target: { value: 'Single recipient' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Confirm waive' }))
  expect(await lastBody(requests)).toEqual({ status: 'waived', waive_reason: 'Single recipient' })

  const waived = { ...CASE.tasks[0], status: 'waived' as const, waive_reason: 'Single recipient' }
  render({ ...CASE, tasks: [waived] })
  expect(screen.getByText(/Waived: Single recipient/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Reopen Identify recipients' }))
  expect(await lastBody(requests)).toEqual({ status: 'open' })
})

test('adds a task that depends on another', async () => {
  const requests = api()
  render()
  fireEvent.change(screen.getByLabelText('New task title'), { target: { value: 'Brief the CISO' } })
  fireEvent.change(screen.getByLabelText('New task assignee'), { target: { value: 'u1' } })
  const dependsOn = screen.getByLabelText('New task depends on') as HTMLSelectElement
  dependsOn.options[1].selected = true
  fireEvent.change(dependsOn)
  fireEvent.click(screen.getByRole('button', { name: 'Add task' }))
  expect(await lastBody(requests)).toEqual({
    title: 'Brief the CISO',
    description: '',
    checklist: [],
    assignee_id: 'u1',
    due_at: null,
    depends_on: ['t2'],
  })
})

test('reports a rejected change', async () => {
  mockApi({
    [`PATCH ${TASKS}/t1`]: () =>
      json({ detail: [{ loc: ['body'], msg: 'Value error, the task is blocked', type: 'value_error' }] }, 422),
  })
  render()
  fireEvent.change(screen.getByLabelText('Status of Identify recipients'), { target: { value: 'done' } })
  expect(await screen.findByRole('alert')).toHaveTextContent('the task is blocked')
})

test('read-only when the case cannot change', () => {
  render(CASE, false)
  expect(screen.getByLabelText('Status of Identify recipients')).toBeDisabled()
  expect(screen.queryByRole('button', { name: /Waive/ })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Add task' })).not.toBeInTheDocument()
})
