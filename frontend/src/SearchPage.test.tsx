import { fireEvent, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { TENANT } from './cases/fixtures'
import { SearchPage } from './SearchPage'
import { json, mockApi, renderInTenant } from './testing'

afterEach(() => vi.unstubAllGlobals())

const HITS = [
  { case_number: 7, case_title: 'Suspicious email', kind: 'note', excerpt: 'Interviewed the team lead' },
  { case_number: 9, case_title: 'Badge misuse', kind: 'evidence', excerpt: 'badge-log.csv' },
]
const SAVED = [{ id: 's1', name: 'Badge logs', query: 'badge log' }]

function api(saved = SAVED) {
  return mockApi({
    'GET /api/tenants/dr/search': (request) =>
      json(new URL(request.url).searchParams.get('q') === 'nothing' ? [] : HITS),
    'GET /api/tenants/dr/searches': () => json(saved),
    'POST /api/tenants/dr/searches': async (request) => json({ id: 's2', ...(await request.json()) }, 201),
    'DELETE /api/tenants/dr/searches/s1': () => new Response(null, { status: 204 }),
  })
}

function render(path = '/t/dr/search') {
  renderInTenant(<SearchPage />, TENANT, { route: 'search', path })
}

test('searches and links each hit to its case', async () => {
  const requests = api()
  render()
  fireEvent.change(screen.getByLabelText('Search terms'), { target: { value: ' team lead ' } })
  fireEvent.click(screen.getByRole('button', { name: 'Search' }))

  const link = await screen.findByRole('link', { name: '#7 Suspicious email' })
  expect(link).toHaveAttribute('href', '/t/dr/cases/7')
  expect(screen.getByText('Interviewed the team lead')).toBeInTheDocument()
  expect(screen.getByText('evidence')).toHaveClass('badge')
  const search = requests.find((r) => new URL(r.url).pathname.endsWith('/search'))!
  expect(new URL(search.url).searchParams.get('q')).toBe('team lead')
})

test('runs the query from the address and says when nothing matches', async () => {
  api()
  render('/t/dr/search?q=nothing')
  expect(await screen.findByText('No matches.')).toBeInTheDocument()
  expect(screen.getByLabelText('Search terms')).toHaveValue('nothing')
  expect(screen.queryByRole('button', { name: 'Save search' })).not.toBeInTheDocument()
})

test('saves, opens, and deletes saved searches', async () => {
  const requests = api()
  render('/t/dr/search?q=lead')
  await screen.findByRole('link', { name: '#7 Suspicious email' })
  expect(screen.getByRole('link', { name: 'Badge logs' })).toHaveAttribute('href', '/t/dr/search?q=badge%20log')

  fireEvent.change(screen.getByLabelText('Saved search name'), { target: { value: 'Leads' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save search' }))
  await vi.waitFor(() => expect(requests.some((r) => r.method === 'POST')).toBe(true))
  expect(await requests.find((r) => r.method === 'POST')!.json()).toEqual({ name: 'Leads', query: 'lead' })

  fireEvent.click(screen.getByRole('button', { name: 'Delete Badge logs' }))
  await vi.waitFor(() => expect(requests.some((r) => r.method === 'DELETE')).toBe(true))
})

test('shows why a save failed', async () => {
  mockApi({
    'GET /api/tenants/dr/search': () => json(HITS),
    'GET /api/tenants/dr/searches': () => json([]),
    'POST /api/tenants/dr/searches': () =>
      json({ detail: [{ loc: ['body', 'name'], msg: 'Value error, a saved search with this name already exists', type: 'value_error' }] }, 422),
  })
  render('/t/dr/search?q=lead')
  fireEvent.change(await screen.findByLabelText('Saved search name'), { target: { value: 'Leads' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save search' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('name: a saved search with this name already exists')
})

test('reports a failed search', async () => {
  mockApi({ 'GET /api/tenants/dr/search': () => json({}, 500), 'GET /api/tenants/dr/searches': () => json([]) })
  render('/t/dr/search?q=lead')
  expect(await screen.findByRole('alert')).toHaveTextContent('Search failed (500)')
})
