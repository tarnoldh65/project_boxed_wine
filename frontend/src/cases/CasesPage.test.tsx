import { fireEvent, screen, within } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, expect, test, vi } from 'vitest'
import type { TenantAccess } from '../api/client'
import { json, mockApi, renderInTenant } from '../testing'
import { CasesPage } from './CasesPage'
import { CASE, CONFIG, MEMBERS, TENANT } from './fixtures'
import { NO_FILTER, PAGE_SIZE, type QueueFilter } from './queue'

afterEach(() => vi.unstubAllGlobals())

function api(cases = [CASE]) {
  return mockApi({
    'GET /api/tenants/dr/config': () => json(CONFIG),
    'GET /api/tenants/dr/members': () => json(MEMBERS),
    'GET /api/tenants/dr/cases': () => json({ cases, total: cases.length }),
    'POST /api/tenants/dr/cases': async (request) =>
      json({ ...CASE, number: 8, ...(await request.json()) }, 201),
  })
}

function Queue({ initial, activeCase }: { initial: QueueFilter; activeCase?: number }) {
  const [filter, setFilter] = useState(initial)
  return <CasesPage filter={filter} onFilter={setFilter} activeCase={activeCase} />
}

function render(tenant: TenantAccess = TENANT, initial = NO_FILTER, activeCase?: number) {
  renderInTenant(<Queue initial={initial} activeCase={activeCase} />, tenant)
}

test('lists cases with state, severity, and owner names', async () => {
  api([CASE, { ...CASE, number: 8, title: 'Ransom note', owner_id: null, restricted: true }])
  render()
  const rows = (await screen.findAllByRole('row')).slice(1)
  expect(rows.map((r) => within(r).getAllByRole('cell').map((c) => c.textContent))).toEqual([
    ['7', 'Suspicious email', 'New', 'P1 high', 'pending', 'J Doe', expect.any(String)],
    ['8', 'Ransom note (restricted)', 'New', 'P1 high', 'pending', 'Unassigned', expect.any(String)],
  ])
  expect(screen.getByRole('link', { name: 'Suspicious email' })).toHaveAttribute('href', '/t/dr/cases/7')
})

function lastQuery(requests: Request[]) {
  const lists = requests.filter((r) => new URL(r.url).pathname === '/api/tenants/dr/cases')
  return Object.fromEntries(new URL(lists.at(-1)!.url).searchParams)
}

test('asks the server for each filter', async () => {
  const requests = api()
  render()
  await screen.findByRole('link', { name: 'Suspicious email' })
  expect(lastQuery(requests)).toEqual({ sort: 'created', descending: 'true', limit: '100', offset: '0' })

  fireEvent.change(screen.getByLabelText('State'), { target: { value: 'triage' } })
  await vi.waitFor(() => expect(lastQuery(requests)).toMatchObject({ state: 'triage' }))
  fireEvent.change(screen.getByLabelText('Severity'), { target: { value: 'high' } })
  await vi.waitFor(() => expect(lastQuery(requests)).toMatchObject({ state: 'triage', severity: 'high' }))
  fireEvent.change(screen.getByLabelText('Owner'), { target: { value: '*unassigned' } })
  await vi.waitFor(() => expect(lastQuery(requests)).toMatchObject({ unassigned: 'true' }))

  fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }))
  await vi.waitFor(() => expect(lastQuery(requests)).not.toHaveProperty('state'))
})

test('waits for a pause in typing before filtering by text', async () => {
  const requests = api()
  render()
  await screen.findByRole('link', { name: 'Suspicious email' })
  const box = screen.getByLabelText('Filter cases')
  fireEvent.change(box, { target: { value: 'p' } })
  fireEvent.change(box, { target: { value: 'ph' } })
  fireEvent.change(box, { target: { value: 'phish' } })
  await vi.waitFor(() => expect(lastQuery(requests)).toMatchObject({ text: 'phish' }))
  expect(requests.filter((r) => r.url.includes('text='))).toHaveLength(1)
})

test('says when nothing matches, and how many do', async () => {
  mockApi({
    'GET /api/tenants/dr/config': () => json(CONFIG),
    'GET /api/tenants/dr/members': () => json(MEMBERS),
    'GET /api/tenants/dr/cases': (request) =>
      new URL(request.url).searchParams.get('severity') === 'low'
        ? json({ cases: [], total: 0 })
        : json({ cases: [CASE], total: 1 }),
  })
  render(TENANT, { ...NO_FILTER, severity: 'low' })
  expect(await screen.findByText('No cases match these filters.')).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Severity'), { target: { value: 'high' } })
  expect(await screen.findByText('1 case')).toBeInTheDocument()
})

test('sorts through the server, and reverses on a second click', async () => {
  const requests = api()
  render()
  await screen.findByRole('link', { name: 'Suspicious email' })
  expect(screen.getByRole('columnheader', { name: 'Created' })).toHaveAttribute('aria-sort', 'descending')

  fireEvent.click(screen.getByRole('button', { name: 'Title' }))
  await vi.waitFor(() => expect(lastQuery(requests)).toMatchObject({ sort: 'title', descending: 'false' }))
  expect(await screen.findByRole('columnheader', { name: 'Title' })).toHaveAttribute('aria-sort', 'ascending')
  fireEvent.click(screen.getByRole('button', { name: 'Title' }))
  await vi.waitFor(() => expect(lastQuery(requests)).toMatchObject({ sort: 'title', descending: 'true' }))
})

test('marks each row with its severity and highlights the open case', async () => {
  api([CASE, { ...CASE, number: 8, title: 'Ransom note', severity: 'critical' }])
  render(TENANT, NO_FILTER, 8)
  const active = await screen.findByRole('link', { name: 'Ransom note' })
  expect(active).toHaveAttribute('aria-current', 'true')
  const row = active.closest('tr')!
  expect(row).toHaveClass('selected')
  expect(within(row).getByText('8')).toHaveClass('marker-critical')
})

test('filters from the dashboard show as removable chips', async () => {
  const requests = api()
  render(TENANT, { ...NO_FILTER, sla: 'breached', age: 'under_1_day' })
  fireEvent.click(await screen.findByRole('button', { name: /under 1 day old/ }))
  await vi.waitFor(() => expect(lastQuery(requests)).not.toHaveProperty('age'))
  expect(lastQuery(requests)).toMatchObject({ sla: 'breached' })
  fireEvent.click(await screen.findByRole('button', { name: /SLA breached/ }))
  await vi.waitFor(() => expect(lastQuery(requests)).not.toHaveProperty('sla'))
})

test('pages through every match, so older cases stay reachable', async () => {
  const page = (offset: number) => ({
    cases: [{ ...CASE, number: offset + 1, title: `Case at ${offset}` }],
    total: PAGE_SIZE + 1,
  })
  const requests = mockApi({
    'GET /api/tenants/dr/config': () => json(CONFIG),
    'GET /api/tenants/dr/members': () => json(MEMBERS),
    'GET /api/tenants/dr/cases': (request) => json(page(Number(new URL(request.url).searchParams.get('offset')))),
  })
  render()
  expect(await screen.findByRole('link', { name: 'Case at 0' })).toBeInTheDocument()
  expect(screen.getByText('Showing 1 of 101')).toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: 'Show more' }))
  expect(await screen.findByRole('link', { name: `Case at ${PAGE_SIZE}` })).toBeInTheDocument()
  expect(lastQuery(requests)).toMatchObject({ offset: String(PAGE_SIZE) })
  expect(screen.getByRole('link', { name: 'Case at 0' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Show more' })).not.toBeInTheDocument()
})

test('creates a case from a template and opens it', async () => {
  const requests = api([])
  render()
  fireEvent.click(await screen.findByText('New case'))
  fireEvent.change(screen.getByLabelText('Type'), { target: { value: 'bec' } })
  fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'CFO wire request' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create case' }))

  expect(await screen.findByText('Now at /t/dr/cases/8')).toBeInTheDocument()
  const post = requests.find((r) => r.method === 'POST')!
  expect(await post.json()).toEqual({ template: 'bec', title: 'CFO wire request', description: '' })
})

test('read-only roles cannot create cases', async () => {
  api()
  render({ ...TENANT, permissions: ['cases:read'] })
  await screen.findByRole('link', { name: 'Suspicious email' })
  expect(screen.queryByText('New case')).not.toBeInTheDocument()
})

test('reports a failed load', async () => {
  mockApi({
    'GET /api/tenants/dr/config': () => json(CONFIG),
    'GET /api/tenants/dr/members': () => json(MEMBERS),
    'GET /api/tenants/dr/cases': () => json({}, 500),
  })
  render()
  expect(await screen.findByRole('alert')).toHaveTextContent('Could not load cases (500)')
})
