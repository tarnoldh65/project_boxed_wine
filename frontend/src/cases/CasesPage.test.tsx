import { fireEvent, screen, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { json, mockApi, renderInTenant } from '../testing'
import { CasesPage } from './CasesPage'
import { CASE, CONFIG, MEMBERS, TENANT } from './fixtures'

afterEach(() => vi.unstubAllGlobals())

function api(cases = [CASE]) {
  return mockApi({
    'GET /api/tenants/dr/config': () => json(CONFIG),
    'GET /api/tenants/dr/members': () => json(MEMBERS),
    'GET /api/tenants/dr/cases': (request) => {
      const state = new URL(request.url).searchParams.get('state')
      return json(cases.filter((c) => !state || c.state === state))
    },
    'POST /api/tenants/dr/cases': async (request) =>
      json({ ...CASE, number: 8, ...(await request.json()) }, 201),
  })
}

test('lists cases with state, severity, and owner names', async () => {
  api([CASE, { ...CASE, number: 8, title: 'Ransom note', owner_id: null, restricted: true }])
  renderInTenant(<CasesPage />, TENANT)
  const rows = (await screen.findAllByRole('row')).slice(1)
  expect(rows.map((r) => within(r).getAllByRole('cell').map((c) => c.textContent))).toEqual([
    ['7', 'Suspicious email', 'New', 'P1 high', 'J Doe', expect.any(String)],
    ['8', 'Ransom note (restricted)', 'New', 'P1 high', 'Unassigned', expect.any(String)],
  ])
  expect(screen.getByRole('link', { name: 'Suspicious email' })).toHaveAttribute('href', '/t/dr/cases/7')
})

test('filters by state', async () => {
  const requests = api()
  renderInTenant(<CasesPage />, TENANT)
  await screen.findByRole('link', { name: 'Suspicious email' })
  fireEvent.change(screen.getByLabelText('State'), { target: { value: 'triage' } })
  expect(await screen.findByText('No cases yet.')).toBeInTheDocument()
  expect(requests.at(-1)!.url).toContain('state=triage')
})

test('creates a case from a template and opens it', async () => {
  const requests = api([])
  renderInTenant(<CasesPage />, TENANT)
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
  renderInTenant(<CasesPage />, { ...TENANT, permissions: ['cases:read'] })
  await screen.findByRole('link', { name: 'Suspicious email' })
  expect(screen.queryByText('New case')).not.toBeInTheDocument()
})

test('reports a failed load', async () => {
  mockApi({
    'GET /api/tenants/dr/config': () => json(CONFIG),
    'GET /api/tenants/dr/members': () => json(MEMBERS),
    'GET /api/tenants/dr/cases': () => json({}, 500),
  })
  renderInTenant(<CasesPage />, TENANT)
  expect(await screen.findByRole('alert')).toHaveTextContent('Could not load cases (500)')
})
