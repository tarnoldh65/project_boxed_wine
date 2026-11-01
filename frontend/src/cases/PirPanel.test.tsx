import { fireEvent, screen, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import type { Case } from '../api/client'
import { json, mockApi, renderWithQuery } from '../testing'
import { CASE, MEMBERS } from './fixtures'
import { PirPanel } from './PirPanel'

afterEach(() => vi.unstubAllGlobals())

const BASE = '/api/tenants/dr/cases/7/pir'

function render(current: Case = CASE, canWrite = true) {
  renderWithQuery(<PirPanel slug="dr" current={current} members={MEMBERS} canWrite={canWrite} />)
}

async function lastRequest(requests: Request[], method: string, path: string) {
  await vi.waitFor(() =>
    expect(requests.some((r) => r.method === method && new URL(r.url).pathname === path)).toBe(true),
  )
  return requests.filter((r) => r.method === method && new URL(r.url).pathname === path).at(-1)!
}

test('shows the review, its completeness, and action items', () => {
  render()
  expect(screen.getByText(/Incomplete: every section must be filled in/)).toBeInTheDocument()
  expect(screen.getByLabelText('Root cause')).toHaveValue('Mail filter allowed the lookalike domain')
  expect(screen.getByLabelText('What went well')).toHaveValue('Fast purge')
  const row = screen.getByText('Block lookalike domains').closest('tr')!
  expect(within(row).getByText('A Smith')).toBeInTheDocument()
  expect(within(row).getByText('open')).toBeInTheDocument()
})

test('saves the review', async () => {
  const requests = mockApi({ [`PUT ${BASE}`]: () => json({ ...CASE, pir: { ...CASE.pir, complete: true } }) })
  render()
  fireEvent.change(screen.getByLabelText('Contributing factors'), { target: { value: 'No DMARC' } })
  fireEvent.change(screen.getByLabelText('What did not go well'), { target: { value: 'Slow triage' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save review' }))

  expect(await (await lastRequest(requests, 'PUT', BASE)).json()).toEqual({
    root_cause: 'Mail filter allowed the lookalike domain',
    contributing_factors: 'No DMARC',
    went_well: 'Fast purge',
    went_poorly: 'Slow triage',
  })
})

test('adds an action item and marks it done', async () => {
  const requests = mockApi({
    [`POST ${BASE}/actions`]: () => json(CASE, 201),
    [`PATCH ${BASE}/actions/p1`]: () => json(CASE),
  })
  render()
  fireEvent.change(screen.getByLabelText('Action item'), { target: { value: 'Enable DMARC' } })
  fireEvent.change(screen.getByLabelText('Action owner'), { target: { value: 'u1' } })
  fireEvent.change(screen.getByLabelText('Action due'), { target: { value: '2026-12-01T09:00' } })
  fireEvent.click(screen.getByRole('button', { name: 'Add action item' }))
  expect(await (await lastRequest(requests, 'POST', `${BASE}/actions`)).json()).toEqual({
    title: 'Enable DMARC',
    owner_id: 'u1',
    due_at: new Date('2026-12-01T09:00').toISOString(),
  })

  fireEvent.click(screen.getByRole('button', { name: 'Mark done Block lookalike domains' }))
  expect(await (await lastRequest(requests, 'PATCH', `${BASE}/actions/p1`)).json()).toEqual({ done: true })
})

test('reopens a finished action and adds one without owner or due date', async () => {
  const done = { ...CASE.pir.actions[0], done_at: '2026-11-01T10:00:00Z' }
  const requests = mockApi({
    [`PATCH ${BASE}/actions/p1`]: () => json(CASE),
    [`POST ${BASE}/actions`]: () => json({ detail: 'Not allowed' }, 403),
  })
  render({ ...CASE, pir: { ...CASE.pir, complete: true, actions: [done] } })
  expect(screen.getByText('Complete.')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Reopen Block lookalike domains' }))
  expect(await (await lastRequest(requests, 'PATCH', `${BASE}/actions/p1`)).json()).toEqual({ done: false })

  fireEvent.change(screen.getByLabelText('Action item'), { target: { value: 'Train staff' } })
  fireEvent.click(screen.getByRole('button', { name: 'Add action item' }))
  expect(await (await lastRequest(requests, 'POST', `${BASE}/actions`)).json()).toEqual({
    title: 'Train staff',
    owner_id: null,
    due_at: null,
  })
  expect(await screen.findByRole('alert')).toHaveTextContent('Request failed (403)')
})

test('read-only users see the review without controls', () => {
  render({ ...CASE, pir: { ...CASE.pir, actions: [] } }, false)
  expect(screen.getByLabelText('Root cause')).toHaveAttribute('readonly')
  expect(screen.queryByRole('button', { name: 'Save review' })).not.toBeInTheDocument()
  expect(screen.queryByLabelText('Action item')).not.toBeInTheDocument()
  expect(screen.getByText('No action items.')).toBeInTheDocument()
})
