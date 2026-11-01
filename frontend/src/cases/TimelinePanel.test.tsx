import { fireEvent, screen, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import type { Case } from '../api/client'
import { json, mockApi, renderWithQuery } from '../testing'
import { CommentsPanel } from './CommentsPanel'
import { CASE, MEMBERS } from './fixtures'
import { TimelinePanel } from './TimelinePanel'

afterEach(() => vi.unstubAllGlobals())

const BASE = '/api/tenants/dr/cases/7'

function render(current: Case = CASE, editable = true) {
  renderWithQuery(<TimelinePanel slug="dr" current={current} members={MEMBERS} editable={editable} />)
}

async function sent(requests: Request[], method: string, path: string) {
  await vi.waitFor(() => expect(requests.some((r) => r.method === method && new URL(r.url).pathname === path)).toBe(true))
  return requests.filter((r) => r.method === method && new URL(r.url).pathname === path).at(-1)!
}

test('shows events in order with milestones, links, and dwell time', () => {
  render()
  const rows = screen.getAllByRole('row').slice(1)
  expect(within(rows[0]).getByText('First phish click')).toBeInTheDocument()
  expect(within(rows[0]).getByText('evil[.]example')).toBeInTheDocument()
  expect(within(rows[0]).getByText(/J Doe/)).toBeInTheDocument()
  expect(within(rows[1]).getByText('host: web-01')).toBeInTheDocument()
  expect(within(rows[1]).getByText(/API token/)).toBeInTheDocument()
  expect(screen.getByText('2.3 days')).toBeInTheDocument() // first compromise to detection
  expect(screen.getAllByText('not recorded')).toHaveLength(2)
})

test('records an event with a milestone and a related asset', async () => {
  const requests = mockApi({ [`POST ${BASE}/timeline`]: () => json(CASE, 201) })
  render()
  fireEvent.change(screen.getByLabelText('Event time'), { target: { value: '2026-10-30T14:00' } })
  fireEvent.change(screen.getByLabelText('Event source'), { target: { value: 'Firewall' } })
  fireEvent.change(screen.getByLabelText('Event description'), { target: { value: 'Egress blocked' } })
  const milestones = within(screen.getByLabelText('Milestone')).getAllByRole('option').map((o) => o.textContent)
  expect(milestones).toEqual(['No milestone', 'Containment', 'Eradication'])
  fireEvent.change(screen.getByLabelText('Milestone'), { target: { value: 'containment' } })
  fireEvent.change(screen.getByLabelText('Asset'), { target: { value: 'h1' } })
  fireEvent.click(screen.getByRole('button', { name: 'Add event' }))

  expect(await (await sent(requests, 'POST', `${BASE}/timeline`)).json()).toEqual({
    occurred_at: new Date('2026-10-30T14:00').toISOString(),
    source: 'Firewall',
    description: 'Egress blocked',
    milestone: 'containment',
    observable_id: null,
    asset_id: 'h1',
    evidence_id: null,
  })
})

test('removes an event and reports errors', async () => {
  const requests = mockApi({
    [`DELETE ${BASE}/timeline/tl2`]: () => new Response(null, { status: 204 }),
    [`GET ${BASE}`]: () => json(CASE),
    [`POST ${BASE}/timeline`]: () =>
      json({ detail: [{ loc: ['body'], msg: 'Value error, the case already has a detection milestone', type: 'value_error' }] }, 422),
  })
  render()
  fireEvent.click(screen.getByRole('button', { name: 'Remove event Beacon observed' }))
  await sent(requests, 'GET', BASE)

  fireEvent.change(screen.getByLabelText('Event time'), { target: { value: '2026-10-30T14:00' } })
  fireEvent.change(screen.getByLabelText('Event source'), { target: { value: 'x' } })
  fireEvent.change(screen.getByLabelText('Event description'), { target: { value: 'y' } })
  fireEvent.click(screen.getByRole('button', { name: 'Add event' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('already has a detection milestone')
})

test('read-only users see the timeline only', () => {
  render(CASE, false)
  expect(screen.queryByRole('button', { name: 'Add event' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /Remove event/ })).not.toBeInTheDocument()
})

test('comments show authors and accept new posts', async () => {
  const requests = mockApi({ [`POST ${BASE}/comments`]: () => json(CASE, 201) })
  renderWithQuery(<CommentsPanel slug="dr" current={CASE} members={MEMBERS} canWrite />)
  expect(screen.getByText('A Smith')).toBeInTheDocument()
  expect(screen.getByText('Isolating web-01')).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('New comment'), { target: { value: '<b>not bold</b>' } })
  fireEvent.click(screen.getByRole('button', { name: 'Post comment' }))
  expect(await (await sent(requests, 'POST', `${BASE}/comments`)).json()).toEqual({ body: '<b>not bold</b>' })
})

test('a case with nothing to link sends no links', async () => {
  const requests = mockApi({ [`POST ${BASE}/timeline`]: () => json(CASE, 201) })
  render({ ...CASE, observables: [], assets: [], evidence: [], timeline: [] })
  expect(screen.queryByLabelText('Asset')).not.toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Event time'), { target: { value: '2026-10-30T14:00' } })
  fireEvent.change(screen.getByLabelText('Event source'), { target: { value: 'EDR' } })
  fireEvent.change(screen.getByLabelText('Event description'), { target: { value: 'Beacon' } })
  fireEvent.click(screen.getByRole('button', { name: 'Add event' }))
  expect(await (await sent(requests, 'POST', `${BASE}/timeline`)).json()).toMatchObject({
    milestone: null,
    observable_id: null,
    asset_id: null,
    evidence_id: null,
  })
})

test('dwell time runs from first compromise to eradication', () => {
  const eradicated = { ...CASE.timeline[1], id: 'tl3', milestone: 'eradication' as const, occurred_at: '2026-11-01T06:15:00Z' }
  render({ ...CASE, timeline: [...CASE.timeline, eradicated] })
  expect(screen.getByText('Time to detect').nextElementSibling).toHaveTextContent('2.3 days')
  expect(screen.getByText('Dwell time').nextElementSibling).toHaveTextContent('4.0 days')
})
