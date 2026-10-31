import { screen, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { json, mockApi, renderInTenant } from '../testing'
import { TENANT } from './fixtures'
import { ObservablePage } from './ObservablePage'

afterEach(() => vi.unstubAllGlobals())

function open() {
  renderInTenant(<ObservablePage />, TENANT, {
    path: '/t/dr/observables/o1',
    route: 'observables/:observableId',
  })
}

test('lists every case the observable appears in', async () => {
  mockApi({
    'GET /api/tenants/dr/observables/o1': () =>
      json({
        id: 'o1',
        type: 'domain',
        value: 'evil.example',
        defanged: 'evil[.]example',
        cases: [
          { number: 9, title: 'Campaign', state: 'triage', severity: 'high', created_at: '2026-10-31T10:00:00Z' },
          { number: 7, title: 'First report', state: 'closed', severity: 'low', created_at: '2026-10-30T10:00:00Z' },
        ],
      }),
  })
  open()
  expect(await screen.findByRole('heading', { name: 'domain: evil[.]example' })).toBeInTheDocument()
  expect(screen.getByText('Seen in 2 cases.')).toBeInTheDocument()
  const rows = screen.getAllByRole('row').slice(1)
  expect(within(rows[0]).getByRole('link', { name: 'Campaign' })).toHaveAttribute('href', '/t/dr/cases/9')
})

test('reports an unknown observable', async () => {
  mockApi({ 'GET /api/tenants/dr/observables/o1': () => json({ detail: 'Observable not found' }, 404) })
  open()
  expect(await screen.findByRole('alert')).toHaveTextContent('Observable not found')
})
