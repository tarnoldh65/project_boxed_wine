import { fireEvent, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import type { Case } from '../api/client'
import { json, mockApi, renderInTenant } from '../testing'
import { CasePage } from './CasePage'
import { CASE, CONFIG, MEMBERS, TENANT } from './fixtures'

afterEach(() => vi.unstubAllGlobals())

const BASE = '/api/tenants/dr/cases/7'

function api(current: Case = CASE, extra: Record<string, (r: Request) => Response | Promise<Response>> = {}) {
  let shown = current
  return mockApi({
    'GET /api/tenants/dr/config': () => json(CONFIG),
    'GET /api/tenants/dr/members': () => json(MEMBERS),
    [`GET ${BASE}`]: () => json(shown),
    [`PATCH ${BASE}`]: async (request) => {
      shown = { ...shown, ...(await request.json()), updated_at: new Date().toISOString() }
      return json(shown)
    },
    ...extra,
  })
}

function open(permissions = TENANT.permissions) {
  renderInTenant(<CasePage />, { ...TENANT, permissions }, { path: '/t/dr/cases/7', route: 'cases/:number' })
}

async function sent(requests: Request[], method: string, path: string): Promise<Request> {
  const matches = () => requests.filter((r) => r.method === method && new URL(r.url).pathname === path)
  await vi.waitFor(() => expect(matches()).not.toHaveLength(0))
  return matches().at(-1)!
}

async function body(requests: Request[], method: string, path: string) {
  return (await sent(requests, method, path)).json()
}

test('shows the case, its alerts, and its assets', async () => {
  api()
  open()
  expect(await screen.findByRole('heading', { name: '#7 Suspicious email' })).toBeInTheDocument()
  expect(screen.getByText(/P1 high \| New \| source email/)).toBeInTheDocument()
  expect(screen.getByText(/Invoice overdue/)).toBeInTheDocument()
  expect(screen.getByText(/billing@evil.example/)).toBeInTheDocument()
  expect(screen.getByText(/host: web-01 \(CMDB CI-1\)/)).toBeInTheDocument()
  expect(screen.getByLabelText('Owner')).toHaveValue('u1')
  expect(screen.queryByText('Restricted')).not.toBeInTheDocument()
})

test('saves only the changed fields', async () => {
  const requests = api()
  open()
  fireEvent.change(await screen.findByLabelText('Title'), { target: { value: 'Credential phish' } })
  fireEvent.change(screen.getByLabelText('Vector'), { target: { value: 'email' } })
  fireEvent.change(screen.getByLabelText('Records'), { target: { value: '12' } })
  fireEvent.change(screen.getByLabelText('Handler'), { target: { value: 'asmith' } })
  fireEvent.click(screen.getByLabelText('PII'))
  fireEvent.change(screen.getByLabelText('ATT&CK techniques'), { target: { value: 'T1566, T1078' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save case' }))

  expect(await body(requests, 'PATCH', BASE)).toEqual({
    title: 'Credential phish',
    regulated_data: ['pii'],
    attack_techniques: ['T1566', 'T1078'],
    custom: { vector: 'email', records: 12, handler: 'asmith' },
  })
  expect(await screen.findByRole('status')).toHaveTextContent('Saved')
})

test('closing asks for a resolution and overriding asks for a justification', async () => {
  const requests = api()
  open()
  expect(screen.queryByLabelText('Resolution')).not.toBeInTheDocument()
  fireEvent.change(await screen.findByLabelText('State'), { target: { value: 'closed' } })
  fireEvent.change(screen.getByLabelText('Resolution'), { target: { value: 'false_positive' } })
  fireEvent.click(screen.getByLabelText('Override severity'))
  fireEvent.change(screen.getByLabelText('Severity'), { target: { value: 'low' } })
  fireEvent.change(screen.getByLabelText('Justification'), { target: { value: 'Test mailbox' } })
  fireEvent.change(screen.getByLabelText('Owner'), { target: { value: '' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save case' }))

  expect(await body(requests, 'PATCH', BASE)).toEqual({
    state: 'closed',
    resolution: 'false_positive',
    override: { severity: 'low', justification: 'Test mailbox' },
    owner_id: null,
  })
})

test('shows validation errors from the server', async () => {
  api(CASE, {
    [`PATCH ${BASE}`]: () =>
      json({ detail: [{ loc: ['body', 'custom', 'root_cause'], msg: 'required', type: 'value_error' }] }, 422),
  })
  open()
  fireEvent.change(await screen.findByLabelText('State'), { target: { value: 'triage' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save case' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('custom.root_cause: required')
})

test('restricted flag is offered only with the permission', async () => {
  const requests = api()
  open([...TENANT.permissions, 'restricted_case'])
  fireEvent.click(await screen.findByLabelText('Restricted'))
  fireEvent.click(screen.getByRole('button', { name: 'Save case' }))
  expect(await body(requests, 'PATCH', BASE)).toEqual({ restricted: true })
})

test('read-only users see the case without editing controls', async () => {
  api()
  open(['cases:read'])
  expect(await screen.findByLabelText('Title')).toBeDisabled()
  expect(screen.queryByRole('button', { name: 'Save case' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Link asset' })).not.toBeInTheDocument()
  expect(screen.queryByRole('heading', { name: 'Merge or split' })).not.toBeInTheDocument()
})

test('links and removes assets', async () => {
  const requests = api(CASE, {
    [`POST ${BASE}/assets`]: async (request) => json({ id: 'h2', ...(await request.json()) }, 201),
    [`DELETE ${BASE}/assets/h1`]: () => new Response(null, { status: 204 }),
  })
  open()
  fireEvent.change(await screen.findByLabelText('Asset kind'), { target: { value: 'account' } })
  fireEvent.change(screen.getByLabelText('Asset name'), { target: { value: 'svc_backup' } })
  fireEvent.click(screen.getByRole('button', { name: 'Link asset' }))
  expect(await body(requests, 'POST', `${BASE}/assets`)).toEqual({
    kind: 'account',
    name: 'svc_backup',
    external_id: null,
  })

  fireEvent.click(screen.getByRole('button', { name: 'Remove web-01' }))
  await sent(requests, 'DELETE', `${BASE}/assets/h1`)
})

test('merges into another case and follows it', async () => {
  const target = { ...CASE, number: 9, title: 'Campaign' }
  const requests = api(CASE, {
    [`POST ${BASE}/merge`]: () => json(target),
    'GET /api/tenants/dr/cases/9': () => json(target),
  })
  open()
  fireEvent.change(await screen.findByLabelText('Merge into case'), { target: { value: '9' } })
  fireEvent.click(screen.getByRole('button', { name: 'Merge into case' }))
  expect(await body(requests, 'POST', `${BASE}/merge`)).toEqual({ into: 9 })
  expect(await screen.findByRole('heading', { name: '#9 Campaign' })).toBeInTheDocument()
})

test('splits the selected alerts and assets into a new case', async () => {
  const created = { ...CASE, number: 10, title: 'Second wave', alerts: [], assets: [] }
  const requests = api(CASE, {
    [`POST ${BASE}/split`]: () => json(created, 201),
    'GET /api/tenants/dr/cases/10': () => json(created),
  })
  open()
  const button = await screen.findByRole('button', { name: 'Split selected into new case' })
  expect(button).toBeDisabled()
  fireEvent.click(screen.getByLabelText('Select alert Invoice overdue'))
  fireEvent.click(screen.getByLabelText('Select asset web-01'))
  fireEvent.click(screen.getByLabelText('Select observable evil[.]example'))
  fireEvent.change(screen.getByLabelText('New case title'), { target: { value: 'Second wave' } })
  fireEvent.click(button)

  expect(await body(requests, 'POST', `${BASE}/split`)).toEqual({
    title: 'Second wave',
    alert_ids: ['a1'],
    asset_ids: ['h1'],
    observable_ids: ['o1'],
  })
  expect(await screen.findByRole('heading', { name: '#10 Second wave' })).toBeInTheDocument()
})

test('reports a failed merge', async () => {
  api(CASE, {
    [`POST ${BASE}/merge`]: () =>
      json({ detail: [{ loc: ['body', 'into'], msg: 'only open cases can be merged', type: 'value_error' }] }, 422),
  })
  open()
  fireEvent.change(await screen.findByLabelText('Merge into case'), { target: { value: '3' } })
  fireEvent.click(screen.getByRole('button', { name: 'Merge into case' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('into: only open cases can be merged')
})

test('links to the case a merged case went into', async () => {
  api({ ...CASE, state: 'closed', resolution: 'merged', merged_into: 9 })
  open()
  expect(await screen.findByRole('link', { name: '#9' })).toHaveAttribute('href', '/t/dr/cases/9')
  expect(screen.queryByRole('heading', { name: 'Merge or split' })).not.toBeInTheDocument()
})

test('reports a missing case', async () => {
  mockApi({
    'GET /api/tenants/dr/config': () => json(CONFIG),
    'GET /api/tenants/dr/members': () => json(MEMBERS),
  })
  open()
  expect(await screen.findByRole('alert')).toHaveTextContent('Case not found')
})

test('shows SLA clocks and acknowledges the case', async () => {
  const requests = api(CASE, {
    [`POST ${BASE}/acknowledge`]: () => json({ ...CASE, acknowledged_at: '2026-10-31T10:05:00Z' }),
  })
  open()
  const sla = await screen.findByRole('table', { name: 'SLA' })
  expect(sla).toHaveTextContent('acknowledge warning')
  expect(sla).toHaveTextContent('triage pending')
  fireEvent.click(screen.getByRole('button', { name: 'Acknowledge' }))
  await sent(requests, 'POST', `${BASE}/acknowledge`)
  await vi.waitFor(() => expect(screen.queryByRole('button', { name: 'Acknowledge' })).not.toBeInTheDocument())
})

test('declares a major incident', async () => {
  const declared = { ...CASE, major_incident_at: '2026-10-31T11:00:00Z', major_incident_reason: 'Payroll down' }
  const requests = api(CASE, { [`POST ${BASE}/major-incident`]: () => json(declared) })
  open()
  fireEvent.change(await screen.findByLabelText('Major incident reason'), { target: { value: 'Payroll down' } })
  fireEvent.click(screen.getByRole('button', { name: 'Declare major incident' }))
  expect(await body(requests, 'POST', `${BASE}/major-incident`)).toEqual({ reason: 'Payroll down' })
  expect(await screen.findByText(/Major incident declared .*: Payroll down/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Declare major incident' })).not.toBeInTheDocument()
})

test('closed cases offer no workflow actions', async () => {
  api({ ...CASE, state: 'closed', resolution: 'benign' })
  open()
  await screen.findByRole('heading', { name: '#7 Suspicious email' })
  expect(screen.queryByRole('button', { name: 'Acknowledge' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Declare major incident' })).not.toBeInTheDocument()
  expect(screen.getByLabelText('Status of Identify recipients')).toBeDisabled()
})
