import { fireEvent, screen, within } from '@testing-library/react'
import { useParams } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import type { Case } from '../api/client'
import { json, mockApi, renderInTenant } from '../testing'
import {
  AlertsFrame,
  AssetsFrame,
  CaseScope,
  DetailsFrame,
  ReportsFrame,
  type CaseView,
} from './CaseFrames'
import { CaseSummary } from './CaseSummary'
import { CASE, CONFIG, MEMBERS, TENANT } from './fixtures'
import { ObservablesPanel } from './ObservablesPanel'
import { TasksPanel } from './TasksPanel'
import { useSplitSelection } from './useCase'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

const onTab = vi.fn()
const onPopOut = vi.fn()
const onReveal = vi.fn()

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

function Frames({ view }: { view: CaseView }) {
  const split = useSplitSelection(view.current.number)
  return (
    <>
      <CaseSummary
        view={view}
        split={split}
        tab="summary"
        popped={['case-notes']}
        onTab={onTab}
        onPopOut={onPopOut}
        onReveal={onReveal}
        renderPart={() => null}
      />
      <DetailsFrame view={view} />
      <TasksPanel slug="dr" current={view.current} members={view.members} editable={view.editable} />
      <AlertsFrame view={view} split={split} />
      <AssetsFrame view={view} split={split} />
      <ObservablesPanel
        slug="dr"
        current={view.current}
        editable={view.canWrite}
        selected={split.observables}
        onSelect={(ids) => split.choose('observables', ids)}
      />
      <ReportsFrame view={view} />
    </>
  )
}

/** The frames of the case in the address, side by side as in the workspace. */
function Routed() {
  const number = Number(useParams().number)
  return (
    <CaseScope key={number} number={number}>
      {(view) => <Frames view={view} />}
    </CaseScope>
  )
}

function open(permissions = TENANT.permissions) {
  renderInTenant(<Routed />, { ...TENANT, permissions }, { path: '/t/dr/cases/7', route: 'cases/:number' })
}

async function sent(requests: Request[], method: string, path: string): Promise<Request> {
  const matches = () => requests.filter((r) => r.method === method && new URL(r.url).pathname === path)
  await vi.waitFor(() => expect(matches()).not.toHaveLength(0))
  return matches().at(-1)!
}

async function body(requests: Request[], method: string, path: string) {
  return (await sent(requests, method, path)).json()
}

/** Open an action's dialog, fill its one field, and confirm. */
async function act(action: string, field: string, value: string) {
  fireEvent.click(await screen.findByRole('button', { name: action }))
  fireEvent.change(screen.getByLabelText(field), { target: { value } })
  fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
}

test('shows the case, its alerts, and its assets', async () => {
  api()
  open()
  expect(await screen.findByRole('heading', { name: '#7 Suspicious email' })).toBeInTheDocument()
  expect(screen.getByText('P1 high')).toHaveClass('badge-high')
  expect(screen.getByText('New', { selector: '.badge' })).toBeInTheDocument()
  expect(screen.getByText('TLP:AMBER')).toHaveClass('badge-medium')
  expect(screen.getByText(/source email/)).toBeInTheDocument()
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

test('closing asks for a resolution', async () => {
  const requests = api()
  open()
  fireEvent.click(await screen.findByRole('button', { name: 'Change state' }))
  expect(screen.queryByLabelText('Resolution')).not.toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('State'), { target: { value: 'closed' } })
  fireEvent.change(screen.getByLabelText('Resolution'), { target: { value: 'false_positive' } })
  fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))

  expect(await body(requests, 'PATCH', BASE)).toEqual({ state: 'closed', resolution: 'false_positive' })
  await vi.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
})

test('moving between open states sends only the state', async () => {
  const requests = api()
  open()
  fireEvent.click(await screen.findByRole('button', { name: 'Change state' }))
  fireEvent.change(screen.getByLabelText('State'), { target: { value: 'triage' } })
  fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
  expect(await body(requests, 'PATCH', BASE)).toEqual({ state: 'triage' })
})

test('a refused state change stays open with the reason', async () => {
  api(CASE, {
    [`PATCH ${BASE}`]: () =>
      json({ detail: [{ loc: ['body', 'state'], msg: 'finish every task first', type: 'value_error' }] }, 422),
  })
  open()
  fireEvent.click(await screen.findByRole('button', { name: 'Change state' }))
  fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
  expect(await within(screen.getByRole('dialog')).findByRole('alert')).toHaveTextContent('finish every task first')
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

test('overriding severity asks for a justification', async () => {
  const requests = api()
  open()
  fireEvent.click(await screen.findByLabelText('Override severity'))
  fireEvent.change(screen.getByLabelText('Severity'), { target: { value: 'low' } })
  fireEvent.change(screen.getByLabelText('Justification'), { target: { value: 'Test mailbox' } })
  fireEvent.change(screen.getByLabelText('Owner'), { target: { value: '' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save case' }))

  expect(await body(requests, 'PATCH', BASE)).toEqual({
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
  fireEvent.change(await screen.findByLabelText('Title'), { target: { value: 'New title' } })
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
  for (const action of ['Change state', 'Merge into case', 'Split selected into new case']) {
    expect(screen.queryByRole('button', { name: action })).not.toBeInTheDocument()
  }
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
  await act('Merge into case', 'Case number', '9')
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
  await act('Split selected into new case', 'New case title', 'Second wave')

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
  await act('Merge into case', 'Case number', '3')
  expect(await within(screen.getByRole('dialog')).findByRole('alert')).toHaveTextContent(
    'into: only open cases can be merged',
  )
})

test('links to the case a merged case went into', async () => {
  api({ ...CASE, state: 'closed', resolution: 'merged', merged_into: 9 })
  open()
  expect(await screen.findByRole('link', { name: '#9' })).toHaveAttribute('href', '/t/dr/cases/9')
  expect(screen.queryByRole('button', { name: 'Merge into case' })).not.toBeInTheDocument()
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
  await act('Declare major incident', 'Major incident reason', 'Payroll down')
  expect(await body(requests, 'POST', `${BASE}/major-incident`)).toEqual({ reason: 'Payroll down' })
  expect(await screen.findByText(/Major incident declared .*: Payroll down/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Declare major incident' })).not.toBeInTheDocument()
})

test('closed cases can still be reopened, but offer no other workflow actions', async () => {
  api({ ...CASE, state: 'closed', resolution: 'benign' })
  open()
  await screen.findByRole('heading', { name: '#7 Suspicious email' })
  expect(screen.queryByRole('button', { name: 'Acknowledge' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Declare major incident' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Change state' })).toBeInTheDocument()
  expect(screen.getByLabelText('Status of Identify recipients')).toBeDisabled()
})

test('places a legal hold with a reason', async () => {
  const requests = api(CASE, {
    [`PUT ${BASE}/legal-hold`]: () => json({ ...CASE, legal_hold: true }),
  })
  open([...TENANT.permissions, 'legal:manage'])
  await act('Place legal hold', 'Legal hold reason', 'Litigation')
  expect(await body(requests, 'PUT', `${BASE}/legal-hold`)).toEqual({ on: true, reason: 'Litigation' })
  expect(await screen.findByText(/Legal hold: evidence/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Lift legal hold' })).toBeInTheDocument()
})

test('only legal hold managers see the control', async () => {
  api()
  open()
  await screen.findByRole('heading', { name: '#7 Suspicious email' })
  expect(screen.queryByRole('button', { name: 'Place legal hold' })).not.toBeInTheDocument()
})

test('legal marks the case privileged', async () => {
  const requests = api(CASE, {
    [`PUT ${BASE}/privilege`]: () => json({ ...CASE, privileged: true }),
  })
  open([...TENANT.permissions, 'legal:manage'])
  await act('Mark privileged', 'Privilege reason', 'Counsel directed')
  expect(await body(requests, 'PUT', `${BASE}/privilege`)).toEqual({ on: true, reason: 'Counsel directed' })
  expect(await screen.findByText('Attorney-Client Privileged / Prepared at Direction of Counsel')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Remove privilege marking' })).toBeInTheDocument()
})

test('offers each report as PDF and DOCX', async () => {
  api()
  open(['cases:read'])
  const pdf = await screen.findByRole('link', { name: 'Executive summary (PDF)' })
  expect(pdf).toHaveAttribute('href', `${BASE}/reports/executive?format=pdf`)
  expect(pdf).toHaveAttribute('download')
  expect(screen.getByRole('link', { name: 'Post-incident review (DOCX)' })).toHaveAttribute(
    'href',
    `${BASE}/reports/pir?format=docx`,
  )
  expect(screen.getAllByRole('link', { name: /\((PDF|DOCX)\)$/ })).toHaveLength(6)
})

test('the privilege marking shows above every frame of a privileged case', async () => {
  api({ ...CASE, privileged: true })
  renderInTenant(
    <>
      <CaseScope number={7}>{() => <p>First frame</p>}</CaseScope>
      <CaseScope number={7}>{() => <p>Second frame</p>}</CaseScope>
    </>,
    TENANT,
  )
  await screen.findByText('Second frame')
  expect(screen.getAllByText('Attorney-Client Privileged / Prepared at Direction of Counsel')).toHaveLength(2)
})

test('tabs show what each section holds, and which are popped out', async () => {
  api()
  open()
  const assets = await screen.findByRole('tab', { name: 'Assets1' })
  expect(screen.getByRole('tab', { name: 'Summary' })).toHaveAttribute('aria-selected', 'true')
  fireEvent.click(assets)
  expect(onTab).toHaveBeenCalledWith('case-assets')

  // A popped-out section's tab brings its frame into view instead.
  const notes = screen.getByRole('tab', { name: /^Notes.*in its own frame/ })
  fireEvent.click(notes)
  expect(onReveal).toHaveBeenCalledWith('case-notes')
  expect(screen.getByRole('tab', { name: 'Reports' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /^Pop out/ })).not.toBeInTheDocument()
})

test('a change from another frame shows in the form without losing what is being typed', async () => {
  const requests = api()
  open()
  fireEvent.change(await screen.findByLabelText('Description'), { target: { value: 'Half-written' } })

  fireEvent.click(screen.getByRole('button', { name: 'Change state' }))
  fireEvent.change(screen.getByLabelText('State'), { target: { value: 'triage' } })
  fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
  await sent(requests, 'PATCH', BASE)
  await vi.waitFor(() => expect(screen.getByText('Triage', { selector: '.badge' })).toBeInTheDocument())

  expect(screen.getByLabelText('Description')).toHaveValue('Half-written')
  fireEvent.click(screen.getByRole('button', { name: 'Save case' }))
  await vi.waitFor(async () =>
    expect(requests.filter((r) => r.method === 'PATCH')).toHaveLength(2),
  )
  expect(await requests.filter((r) => r.method === 'PATCH')[1].json()).toEqual({ description: 'Half-written' })
})
