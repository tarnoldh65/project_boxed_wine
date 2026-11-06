import { fireEvent, screen, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { CASE, CONFIG, MEMBERS, TENANT } from '../cases/fixtures'
import { NO_FILTER, OPEN_STATES, openCasesFilter, UNASSIGNED } from '../cases/queue'
import { json, mockApi, renderInTenant } from '../testing'
import { BarChart } from './BarChart'
import { orderedBars } from './bars'
import { DeadlinesFrame } from './DeadlinesFrame'
import { MetricsFrame } from './MetricsFrame'
import { OverviewFrame } from './OverviewFrame'

afterEach(() => vi.unstubAllGlobals())

const BOARD = {
  open_by_severity: { low: 1, high: 2 },
  open_by_state: { triage: 1, new: 2 },
  sla_status: { pending: 2, breached: 1 },
  sla_compliance: 0.75,
  workload: [
    { owner_id: 'u1', open: 2 },
    { owner_id: null, open: 1 },
  ],
  aging: { over_30_days: 1, under_1_day: 2 },
  open_pir_actions: 4,
}
const NONE = { hours: null, cases: 0 }
const METRICS = {
  since: '2026-08-03T00:00:00Z',
  until: '2026-11-01T00:00:00Z',
  cases: 12,
  mttd: { hours: 30, cases: 4 },
  mtta: { hours: 0.5, cases: 10 },
  mttc: NONE,
  mttr: { hours: 72, cases: 6 },
  dwell_time: NONE,
  closed: 8,
  false_positive_rate: 0.25,
  by_category: { ddos: 3, phishing: 9 },
  by_technique: {},
  recurring: 3,
  recurrence_rate: 0.25,
}

function api(board: unknown = BOARD) {
  return mockApi({
    'GET /api/tenants/dr/config': () => json(CONFIG),
    'GET /api/tenants/dr/members': () => json(MEMBERS),
    'GET /api/tenants/dr/dashboard': () => (board ? json(board) : json({}, 500)),
    'GET /api/tenants/dr/clocks': () => json([{ ...CASE.clocks[0], status: 'warning', case_number: 7 }]),
    'GET /api/tenants/dr/metrics': () => json(METRICS),
  })
}

function rows(caption: string) {
  const table = screen.getByRole('table', { name: caption })
  return within(table)
    .getAllByRole('row')
    .map((r) => r.textContent)
}

function overview(filter = NO_FILTER) {
  const onFilter = vi.fn()
  const onMetrics = vi.fn()
  renderInTenant(<OverviewFrame filter={filter} onFilter={onFilter} onMetrics={onMetrics} />, TENANT)
  return { onFilter, onMetrics }
}

test('shows headline figures and open work in a sensible order', async () => {
  api()
  overview()
  expect(await screen.findByRole('button', { name: 'Open cases 3' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'SLA breached 1' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Unassigned 1' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'SLA compliance, 30 days 75%' })).toBeInTheDocument()
  expect(screen.getByText('Open review actions').closest('.tile')).toHaveTextContent('4')
  await vi.waitFor(() => expect(rows('Open cases by state')).toEqual(['New2', 'Triage1']))
  expect(rows('Open cases by severity')).toEqual(['high2', 'low1'])
  expect(rows('Open cases by SLA status')).toEqual(['breached1', 'pending2'])
  expect(rows('Open cases by age')).toEqual(['Under 1 day2', 'Over 30 days1'])
  await vi.waitFor(() => expect(rows('Analyst workload')).toEqual(['J Doe2', 'Unassigned1']))
})

test('severity and SLA bars carry their status colour; other bars use the accent', async () => {
  api()
  overview()
  const high = await screen.findByRole('button', { name: 'high: 2' })
  expect(high.closest('tr')!.querySelector('.bar')).toHaveClass('bar-high')
  const breached = screen.getByRole('button', { name: 'breached: 1' })
  expect(breached.closest('tr')!.querySelector('.bar')).toHaveClass('bar-critical')
  const age = screen.getByRole('button', { name: 'Under 1 day: 2' })
  expect(age.closest('tr')!.querySelector('.bar')).toHaveClass('bar-accent')
})

test.each([
  ['Open cases 3', {}],
  ['SLA breached 1', { sla: 'breached' }],
  ['Unassigned 1', { owner: UNASSIGNED }],
  ['high: 2', { severity: 'high' }],
  ['pending: 2', { sla: 'pending' }],
  ['Over 30 days: 1', { age: 'over_30_days' }],
])('selecting %s filters the queue', async (name, part) => {
  api()
  const { onFilter } = overview()
  fireEvent.click(await screen.findByRole('button', { name }))
  expect(onFilter).toHaveBeenCalledWith(part)
})

test('selecting a state or an owner filters the queue on it', async () => {
  api()
  const { onFilter } = overview()
  fireEvent.click(await screen.findByRole('button', { name: 'New: 2' }))
  expect(onFilter).toHaveBeenCalledWith({ state: 'new' })
  fireEvent.click(await screen.findByRole('button', { name: 'J Doe: 2' }))
  expect(onFilter).toHaveBeenCalledWith({ owner: 'u1' })
})

test('SLA compliance opens the metrics', async () => {
  api()
  const { onMetrics } = overview()
  fireEvent.click(await screen.findByRole('button', { name: 'SLA compliance, 30 days 75%' }))
  expect(onMetrics).toHaveBeenCalled()
})

test('the figure behind the current filter shows as selected', async () => {
  api()
  overview(openCasesFilter({ severity: 'high' }))
  expect(await screen.findByRole('button', { name: 'high: 2' })).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByRole('button', { name: 'low: 1' })).toHaveAttribute('aria-pressed', 'false')
  expect(screen.getByRole('button', { name: 'Open cases 3' })).toHaveAttribute('aria-pressed', 'false')
})

test('all open cases selects the Open cases figure', async () => {
  api()
  overview({ ...NO_FILTER, state: OPEN_STATES })
  expect(await screen.findByRole('button', { name: 'Open cases 3' })).toHaveAttribute('aria-pressed', 'true')
})

test('reports a failed load', async () => {
  api(null)
  overview()
  expect(await screen.findByRole('alert')).toHaveTextContent('Could not load the dashboard (500)')
})

test('bars scale to the largest value, and an empty chart says so', () => {
  renderInTenant(
    <>
      <BarChart caption="Filled" bars={orderedBars({ a: 1, b: 4 }, ['b', 'a'])} />
      <BarChart caption="Empty" bars={[]} />
    </>,
    TENANT,
  )
  expect(rows('Filled')).toEqual(['b4', 'a1'])
  const widths = [...document.querySelectorAll<HTMLElement>('.bar')].map((bar) => bar.style.width)
  expect(widths).toEqual(['100%', '25%'])
  expect(rows('Empty')).toEqual(['None'])
})

test('keys the order does not list still appear, last', () => {
  expect(orderedBars({ custom: 1, high: 2 }, ['high', 'low']).map((bar) => bar.key)).toEqual(['high', 'custom'])
})

test('lists notification deadlines across cases', async () => {
  api()
  renderInTenant(<DeadlinesFrame />, TENANT)
  const link = await screen.findByRole('link', { name: '#7' })
  expect(link).toHaveAttribute('href', '/t/dr/cases/7')
  const cells = within(link.closest('tr')!).getAllByRole('cell')
  expect(cells[1]).toHaveTextContent('GDPR Art. 33')
  expect(within(cells[3]).getByText('warning')).toHaveClass('badge-medium')
})

test('says when no notification clock is running', async () => {
  mockApi({ 'GET /api/tenants/dr/clocks': () => json([]) })
  renderInTenant(<DeadlinesFrame />, TENANT)
  expect(await screen.findByText('No notification clock is running.')).toBeInTheDocument()
})

test('shows metrics and changes the window', async () => {
  const requests = api()
  renderInTenant(<MetricsFrame />, TENANT)
  await screen.findByRole('table', { name: 'Response times (12 cases)' })
  expect(rows('Response times (12 cases)')).toEqual([
    'Mean time to detect30.0 hours (4 cases)',
    'Mean time to acknowledge0.5 hours (10 cases)',
    'Mean time to containno data',
    'Mean time to resolve3.0 days (6 cases)',
    'Dwell timeno data',
    'False-positive rate25% of 8 closed',
    'Recurrence3 cases (25%)',
  ])
  expect(rows('Cases by category')).toEqual(['phishing9', 'ddos3'])
  expect(rows('Cases by ATT&CK technique')).toEqual(['None'])

  fireEvent.change(screen.getByLabelText(/Cases opened in the last/), { target: { value: '365' } })
  await vi.waitFor(() => expect(requests.some((r) => r.url.endsWith('/metrics?days=365'))).toBe(true))
})
