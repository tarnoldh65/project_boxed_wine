import { fireEvent, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import type { Me } from '../api/client'
import App from '../App'
import { CASE, CONFIG, MEMBERS } from '../cases/fixtures'
import { json, mockApi, renderWithQuery, stubGridWidth } from '../testing'

const ME: Me = {
  id: 'u1',
  username: 'jdoe',
  display_name: 'J Doe',
  email: 'jdoe@example.test',
  is_platform_admin: false,
  tenants: [{ slug: 'dr', name: 'Detect and Respond', permissions: ['cases:read', 'cases:write', 'config:manage'] }],
}
const BASE = '/api/tenants/dr'
const SECOND = { ...CASE, number: 8, title: 'Second incident' }

beforeEach(() => {
  mockApi({
    'GET /api/me': () => json(ME),
    [`GET ${BASE}/cases`]: (request) => {
      const severity = new URL(request.url).searchParams.get('severity')
      const cases = [CASE, SECOND].filter((c) => !severity || c.severity === severity)
      return json({ cases, total: cases.length })
    },
    [`GET ${BASE}/cases/7`]: () => json(CASE),
    [`GET ${BASE}/cases/8`]: () => json(SECOND),
    [`GET ${BASE}/config`]: () => json(CONFIG),
    [`GET ${BASE}/members`]: () => json(MEMBERS),
    [`GET ${BASE}/search`]: () =>
      json([{ case_number: 7, case_title: 'Suspicious email', kind: 'note', excerpt: 'A note' }]),
    [`GET ${BASE}/searches`]: () => json([]),
    [`GET ${BASE}/observables/o1`]: () =>
      json({ id: 'o1', type: 'domain', value: 'evil.example', defanged: 'evil[.]example', cases: [] }),
    [`GET ${BASE}/tokens`]: () => json([]),
    [`GET ${BASE}/cases/7/evidence/e1/custody`]: () =>
      json({ evidence: CASE.evidence[0], chain_intact: true, generated_at: '2026-10-31T12:00:00Z', entries: [] }),
    [`GET ${BASE}/dashboard`]: () =>
      json({
        open_by_severity: { high: 1, low: 1 },
        open_by_state: { new: 2 },
        sla_status: { breached: 1, pending: 1 },
        sla_compliance: 0.5,
        workload: [{ owner_id: 'u1', open: 2 }],
        aging: { under_1_day: 2 },
        open_pir_actions: 0,
      }),
    [`GET ${BASE}/clocks`]: () => json([]),
    [`GET ${BASE}/metrics`]: () =>
      json({
        since: '2026-08-03T00:00:00Z',
        until: '2026-11-01T00:00:00Z',
        cases: 2,
        mttd: { hours: null, cases: 0 },
        mtta: { hours: null, cases: 0 },
        mttc: { hours: null, cases: 0 },
        mttr: { hours: null, cases: 0 },
        dwell_time: { hours: null, cases: 0 },
        closed: 0,
        false_positive_rate: null,
        by_category: {},
        by_technique: {},
        recurring: 0,
        recurrence_rate: null,
      }),
    [`POST ${BASE}/cases`]: () => json(SECOND, 201),
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  localStorage.clear()
})

function open(path = '/t/dr', width = 1280) {
  stubGridWidth(width)
  renderWithQuery(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  )
}

const frame = (name: string | RegExp) => screen.findByRole('region', { name })
const frameNames = () => screen.getAllByRole('region').map((region) => region.getAttribute('aria-labelledby'))

function savedLayout() {
  return JSON.parse(localStorage.getItem('casemgr.layout.u1.dr')!)
}

async function openFramesMenu() {
  fireEvent.click(await screen.findByRole('button', { name: 'Frames' }))
}

test('opens the queue, overview, and deadlines frames by default', async () => {
  open()
  await frame('Case queue')
  expect(frameNames()).toEqual(['frame-title-queue', 'frame-title-overview', 'frame-title-deadlines'])
  expect(await screen.findByRole('link', { name: 'Suspicious email' })).toBeInTheDocument()
})

test('a case link opens the case beside the other frames, and another swaps it', async () => {
  open()
  fireEvent.click(await screen.findByRole('link', { name: 'Suspicious email' }))
  const opened = await frame('Case #7')
  expect(await within(opened).findByRole('heading', { name: '#7 Suspicious email' })).toBeInTheDocument()
  expect(screen.getByRole('region', { name: 'Case queue' })).toBeInTheDocument()

  fireEvent.click(screen.getByRole('link', { name: 'Second incident' }))
  const swapped = await frame('Case #8')
  expect(await within(swapped).findByRole('heading', { name: '#8 Second incident' })).toBeInTheDocument()
  expect(screen.getAllByRole('region').filter((r) => r.id === 'frame-case')).toHaveLength(1)
})

test('selecting a case opens its summary, tasks, and timeline', async () => {
  open()
  fireEvent.click(await screen.findByRole('link', { name: 'Suspicious email' }))
  await frame('Case #7')
  expect(await frame('Tasks #7')).toBeInTheDocument()
  expect(await frame('Timeline #7')).toBeInTheDocument()
  expect(screen.queryByRole('region', { name: 'Notes #7' })).not.toBeInTheDocument()
})

test('summary chips open the other parts of the case, labelled with its number', async () => {
  open('/t/dr/cases/7')
  const summary = await frame('Case #7')
  fireEvent.click(await within(summary).findByRole('button', { name: /^Notes/ }))
  expect(await frame('Notes #7')).toBeInTheDocument()
  expect(within(summary).getByRole('button', { name: /^Notes/ })).toHaveAttribute('aria-pressed', 'true')

  fireEvent.click(screen.getByRole('link', { name: 'Second incident' }))
  expect(await frame('Notes #8')).toBeInTheDocument()
  expect(screen.queryByRole('region', { name: 'Notes #7' })).not.toBeInTheDocument()
})

test('closing the summary closes the whole case', async () => {
  open('/t/dr/cases/7')
  fireEvent.click(await within(await frame('Case #7')).findByRole('button', { name: /^Notes/ }))
  await frame('Notes #7')
  fireEvent.click(screen.getByRole('button', { name: 'Close Case #7' }))
  await vi.waitFor(() => expect(screen.queryByRole('region', { name: /#7$/ })).not.toBeInTheDocument())
})

test('closing one case frame leaves the rest, and it stays closed for the next case', async () => {
  open('/t/dr/cases/7')
  await frame('Tasks #7')
  fireEvent.click(screen.getByRole('button', { name: 'Close Tasks #7' }))
  fireEvent.click(await screen.findByRole('link', { name: 'Second incident' }))
  await frame('Case #8')
  expect(screen.queryByRole('region', { name: 'Tasks #8' })).not.toBeInTheDocument()
})

test('an evidence item opens its chain of custody, which closes with its case', async () => {
  open('/t/dr/cases/7')
  fireEvent.click(await within(await frame('Case #7')).findByRole('button', { name: /^Evidence/ }))
  const evidence = await frame('Evidence #7')
  fireEvent.click(await within(evidence).findByRole('button', { name: 'Custody of mail.eml' }))
  const custody = await frame('Chain of custody')
  expect(within(custody).getByRole('heading', { name: 'mail.eml' })).toBeInTheDocument()
  expect(await within(custody).findByText(/verified intact/)).toBeInTheDocument()

  fireEvent.click(screen.getByRole('link', { name: 'Second incident' }))
  await frame('Case #8')
  await vi.waitFor(() =>
    expect(screen.queryByRole('region', { name: 'Chain of custody' })).not.toBeInTheDocument(),
  )
})

test('opening a case leaves work in progress in other frames alone', async () => {
  open()
  const queue = await frame('Case queue')
  fireEvent.click(await within(queue).findByText('New case'))
  const title = within(queue).getByLabelText('Title')
  fireEvent.change(title, { target: { value: 'Draft title' } })

  fireEvent.click(await screen.findByRole('link', { name: 'Suspicious email' }))
  await frame('Case #7')
  expect(title).toBeInTheDocument()
  expect(title).toHaveValue('Draft title')
})

test('creating a case opens it and leaves a fresh form in the queue', async () => {
  open()
  const queue = await frame('Case queue')
  fireEvent.click(await within(queue).findByText('New case'))
  fireEvent.change(within(queue).getByLabelText('Title'), { target: { value: 'Second incident' } })
  fireEvent.click(within(queue).getByRole('button', { name: 'Create case' }))

  await frame('Case #8')
  expect(within(queue).getByText('New case').closest('details')).not.toHaveAttribute('open')
  expect(within(queue).getByLabelText('Title')).toHaveValue('')
})

test('a case address opens its frame directly', async () => {
  open('/t/dr/cases/7')
  expect(await within(await frame('Case #7')).findByRole('heading', { name: '#7 Suspicious email' })).toBeInTheDocument()
})

test('an observable address opens its frame', async () => {
  open('/t/dr/observables/o1')
  expect(await within(await frame('Observable')).findByText('evil[.]example')).toBeInTheDocument()
})

test('closing the case frame clears the case and its address', async () => {
  open('/t/dr/cases/7')
  await frame('Case #7')
  fireEvent.click(screen.getByRole('button', { name: 'Close Case #7' }))
  expect(await frame('Case queue')).toBeInTheDocument()
  expect(screen.queryByRole('region', { name: /Case #/ })).not.toBeInTheDocument()
  // Closing moved the address on, so the same link opens the frame again.
  fireEvent.click(await screen.findByRole('link', { name: 'Suspicious email' }))
  expect(await frame('Case #7')).toBeInTheDocument()
})

test('global search opens the query in the Search frame, which survives opening a case', async () => {
  open()
  const box = await screen.findByLabelText('Search everything')
  fireEvent.change(box, { target: { value: 'lead' } })
  fireEvent.submit(box.closest('form')!)
  const search = await frame('Search')
  fireEvent.click(await within(search).findByRole('link', { name: /#7 Suspicious email/ }))
  await frame('Case #7')
  expect(within(screen.getByRole('region', { name: 'Search' })).getByLabelText('Search terms')).toHaveValue('lead')
})

test('the launcher closes and reopens frames', async () => {
  open()
  await openFramesMenu()
  const overview = await screen.findByRole('checkbox', { name: 'Overview' })
  expect(overview).toBeChecked()
  fireEvent.click(overview)
  expect(screen.queryByRole('region', { name: 'Overview' })).not.toBeInTheDocument()
  expect(overview).not.toBeChecked()
  fireEvent.click(overview)
  expect(await frame('Overview')).toBeInTheDocument()
})

test('says what to do when every frame is closed', async () => {
  open()
  await frame('Case queue')
  for (const name of ['Case queue', 'Overview', 'Notification deadlines']) {
    fireEvent.click(screen.getByRole('button', { name: `Close ${name}` }))
  }
  expect(await screen.findByText(/No frames are open/)).toBeInTheDocument()
})

test('reset restores the default frames and keeps the open case', async () => {
  open('/t/dr/cases/7')
  await frame('Case #7')
  fireEvent.click(screen.getByRole('button', { name: 'Close Overview' }))
  await openFramesMenu()
  fireEvent.click(screen.getByRole('button', { name: 'Reset layout' }))
  expect(await frame('Overview')).toBeInTheDocument()
  expect(screen.getByRole('region', { name: 'Case #7' })).toBeInTheDocument()
})

test('collapsing hides a frame without losing what was typed', async () => {
  open()
  const queue = await frame('Case queue')
  const details = await within(queue).findByText('New case')
  fireEvent.click(details)
  const title = within(queue).getByLabelText('Title')
  fireEvent.change(title, { target: { value: 'Draft title' } })

  fireEvent.click(screen.getByRole('button', { name: 'Collapse Case queue' }))
  expect(title).not.toBeVisible()
  expect(savedLayout().collapsed).toEqual(['queue'])
  fireEvent.click(screen.getByRole('button', { name: 'Expand Case queue' }))
  expect(title).toBeVisible()
  expect(title).toHaveValue('Draft title')
})

test('maximizing and restoring keep the frame mounted', async () => {
  open()
  const queue = await frame('Case queue')
  fireEvent.click(screen.getByRole('button', { name: 'Maximize Case queue' }))
  expect(queue.parentElement).toHaveClass('frame-maximized')
  fireEvent.click(screen.getByRole('button', { name: 'Restore Case queue' }))
  expect(queue.parentElement).not.toHaveClass('frame-maximized')
  expect(screen.getByRole('region', { name: 'Case queue' })).toBe(queue)
})

test('the arrange menu moves a frame from the keyboard, and the move is saved', async () => {
  open()
  await frame('Case queue')
  const before = savedLayout().layouts.lg.find((item: { i: string }) => item.i === 'overview')
  fireEvent.click(screen.getByRole('button', { name: 'Arrange Overview' }))
  fireEvent.click(screen.getByRole('button', { name: 'Taller' }))
  const after = savedLayout().layouts.lg.find((item: { i: string }) => item.i === 'overview')
  expect(after.h).toBe(before.h + 1)
  // The menu stays open for repeated moves, and Escape closes it.
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(screen.queryByRole('button', { name: 'Taller' })).not.toBeInTheDocument()
})

test('selecting a chart row filters the queue, and selecting it again clears the filter', async () => {
  open()
  const queue = await frame('Case queue')
  await within(queue).findByRole('link', { name: 'Second incident' })
  const bar = await screen.findByRole('button', { name: 'low: 1' })
  fireEvent.click(bar)
  expect(await within(queue).findByText('No cases match these filters.')).toBeInTheDocument()
  expect(bar).toHaveAttribute('aria-pressed', 'true')
  expect(within(queue).getByLabelText('Severity')).toHaveValue('low')
  expect(within(queue).getByLabelText('State')).toHaveValue('*open')

  fireEvent.click(bar)
  expect(await within(queue).findByRole('link', { name: 'Second incident' })).toBeInTheDocument()
  expect(bar).toHaveAttribute('aria-pressed', 'false')
})

test('a chart selection reopens a closed or collapsed queue', async () => {
  open()
  await frame('Case queue')
  fireEvent.click(screen.getByRole('button', { name: 'Close Case queue' }))
  fireEvent.click(await screen.findByRole('button', { name: 'SLA breached 1' }))
  expect(await frame('Case queue')).toBeInTheDocument()
})

test('the SLA compliance figure opens the Metrics frame', async () => {
  open()
  fireEvent.click(await screen.findByRole('button', { name: 'SLA compliance, 30 days 50%' }))
  expect(await within(await frame('Metrics')).findByRole('table', { name: 'Response times (2 cases)' })).toBeInTheDocument()
})

test('a dashboard address opens the Overview after it was closed', async () => {
  open()
  fireEvent.click(await screen.findByRole('button', { name: 'Close Overview' }))
  await vi.waitFor(() => expect(savedLayout().open).toEqual(['queue', 'deadlines']))
  document.body.innerHTML = ''
  open('/t/dr/dashboard')
  expect(await frame('Overview')).toBeInTheDocument()
})

test('a search address opens the Search frame with its query', async () => {
  open('/t/dr/search?q=lead')
  const search = await frame('Search')
  expect(within(search).getByLabelText('Search terms')).toHaveValue('lead')
  expect(await within(search).findByRole('link', { name: /#7 Suspicious email/ })).toBeInTheDocument()
})

test('a saved layout comes back on the next visit', async () => {
  open()
  fireEvent.click(await screen.findByRole('button', { name: 'Close Overview' }))
  await vi.waitFor(() => expect(savedLayout().open).toEqual(['queue', 'deadlines']))
  document.body.innerHTML = ''
  open()
  await frame('Case queue')
  expect(screen.queryByRole('region', { name: 'Overview' })).not.toBeInTheDocument()
})

test('narrow windows offer no sideways moves', async () => {
  open('/t/dr', 400)
  await frame('Case queue')
  fireEvent.click(screen.getByRole('button', { name: 'Arrange Case queue' }))
  expect(screen.getByRole('button', { name: 'Move up' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Move left' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Wider' })).not.toBeInTheDocument()
})

test('the launcher returns from settings to the workspace', async () => {
  open('/t/dr/settings')
  await openFramesMenu()
  fireEvent.click(await screen.findByRole('checkbox', { name: 'Overview' }))
  expect(await frame('Case queue')).toBeInTheDocument()
})
