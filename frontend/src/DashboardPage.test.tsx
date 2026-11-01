import { fireEvent, screen, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { CASE, CONFIG, MEMBERS, TENANT } from './cases/fixtures'
import { DashboardPage } from './DashboardPage'
import { json, mockApi, renderInTenant } from './testing'

afterEach(() => vi.unstubAllGlobals())

const BOARD = {
  open_by_severity: { high: 2, low: 1 },
  open_by_state: { new: 2, triage: 1 },
  sla_status: { breached: 1, pending: 2 },
  sla_compliance: 0.75,
  workload: [
    { owner_id: 'u1', open: 2 },
    { owner_id: null, open: 1 },
  ],
  aging: { under_1_day: 2, over_30_days: 1 },
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
  by_category: { phishing: 9, ddos: 3 },
  by_technique: {},
  recurring: 3,
  recurrence_rate: 0.25,
}

function api() {
  return mockApi({
    'GET /api/tenants/dr/config': () => json(CONFIG),
    'GET /api/tenants/dr/members': () => json(MEMBERS),
    'GET /api/tenants/dr/dashboard': () => json(BOARD),
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

test('shows open work, SLA compliance, and workload', async () => {
  api()
  renderInTenant(<DashboardPage />, TENANT, { route: 'dashboard', path: '/t/dr/dashboard' })
  await screen.findByRole('table', { name: 'Open cases by severity' })
  expect(rows('Open cases by severity')).toEqual(['high2', 'low1'])
  await vi.waitFor(() => expect(rows('Open cases by state')).toEqual(['New2', 'Triage1']))
  expect(rows('Open cases by age')).toEqual(['Under 1 day2', 'Over 30 days1'])
  expect(rows('Analyst workload')).toEqual(['J Doe2', 'Unassigned1'])
  expect(screen.getByText(/SLA compliance, cases closed in the last 30 days: 75%/)).toBeInTheDocument()
  expect(screen.getByText(/Open post-incident review actions: 4/)).toBeInTheDocument()
})

test('lists notification deadlines across cases', async () => {
  api()
  renderInTenant(<DashboardPage />, TENANT, { route: 'dashboard', path: '/t/dr/dashboard' })
  const link = await screen.findByRole('link', { name: '#7' })
  expect(link).toHaveAttribute('href', '/t/dr/cases/7')
  const cells = within(link.closest('tr')!).getAllByRole('cell')
  expect(cells[1]).toHaveTextContent('GDPR Art. 33')
  expect(cells[3]).toHaveClass('sla-warning')
})

test('shows metrics and changes the window', async () => {
  const requests = api()
  renderInTenant(<DashboardPage />, TENANT, { route: 'dashboard', path: '/t/dr/dashboard' })
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

test('reports load failures', async () => {
  mockApi({
    'GET /api/tenants/dr/config': () => json(CONFIG),
    'GET /api/tenants/dr/members': () => json(MEMBERS),
    'GET /api/tenants/dr/dashboard': () => json({}, 500),
    'GET /api/tenants/dr/clocks': () => json([]),
    'GET /api/tenants/dr/metrics': () => json(METRICS),
  })
  renderInTenant(<DashboardPage />, TENANT, { route: 'dashboard', path: '/t/dr/dashboard' })
  expect(await screen.findByRole('alert')).toHaveTextContent('Could not load the dashboard (500)')
  expect(screen.getByText('No notification clock is running.')).toBeInTheDocument()
})
