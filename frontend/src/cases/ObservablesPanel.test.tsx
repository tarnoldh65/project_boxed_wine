import { fireEvent, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import type { Case } from '../api/client'
import { json, mockApi, renderWithQuery } from '../testing'
import { CASE } from './fixtures'
import { ObservablesPanel } from './ObservablesPanel'

afterEach(() => vi.unstubAllGlobals())

const BASE = '/api/tenants/dr/cases/7/observables'
const CANDIDATES = [
  { type: 'url', value: 'https://evil.example/x', defanged: 'hxxps://evil[.]example/x', linked: false },
  { type: 'domain', value: 'evil.example', defanged: 'evil[.]example', linked: true },
  { type: 'ipv4', value: '10.0.0.1', defanged: '10[.]0[.]0[.]1', linked: false },
]

function render(current: Case = CASE, editable = true, onSelect = vi.fn()) {
  renderWithQuery(
    <MemoryRouter>
      <ObservablesPanel slug="dr" current={current} editable={editable} selected={[]} onSelect={onSelect} />
    </MemoryRouter>,
  )
  return onSelect
}

function api() {
  return mockApi({
    [`POST ${BASE}/extract`]: () => json(CANDIDATES),
    [`POST ${BASE}`]: () => json(CASE, 201),
    [`DELETE ${BASE}/o1`]: () => new Response(null, { status: 204 }),
  })
}

async function lastBody(requests: Request[], path: string) {
  await vi.waitFor(() => expect(requests.some((r) => new URL(r.url).pathname === path)).toBe(true))
  return requests.filter((r) => new URL(r.url).pathname === path).at(-1)!.json()
}

test('lists observables defanged, linked to their page, with correlation', () => {
  render()
  expect(screen.getByRole('link', { name: 'evil[.]example' })).toHaveAttribute('href', '/t/dr/observables/o1')
  expect(screen.getByText('seen in 2 other cases')).toBeInTheDocument()
})

test('extracts from pasted text and links only the reviewed candidates', async () => {
  const requests = api()
  render()
  fireEvent.click(screen.getByText('Add observables'))
  fireEvent.change(screen.getByLabelText(/Paste text/), {
    target: { value: 'hxxps://evil[.]example/x 10.0.0.1' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Extract from text' }))
  expect(await lastBody(requests, `${BASE}/extract`)).toEqual({
    text: 'hxxps://evil[.]example/x 10.0.0.1',
  })

  expect(await screen.findByText(/already linked/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('checkbox', { name: 'ipv4 10[.]0[.]0[.]1' }))
  fireEvent.click(screen.getByRole('button', { name: 'Link selected' }))
  expect(await lastBody(requests, BASE)).toEqual({
    observables: [{ type: 'url', value: 'https://evil.example/x' }],
  })
})

test('extracts from the case alerts', async () => {
  const requests = api()
  render()
  fireEvent.click(screen.getByText('Add observables'))
  fireEvent.click(screen.getByRole('button', { name: 'Extract from alerts' }))
  expect(await lastBody(requests, `${BASE}/extract`)).toEqual({ text: null })
})

test('adds an observable by hand and removes one', async () => {
  const requests = api()
  render()
  fireEvent.click(screen.getByText('Add observables'))
  fireEvent.change(screen.getByLabelText('Observable type'), { target: { value: 'cve' } })
  fireEvent.change(screen.getByLabelText('Observable value'), { target: { value: 'cve-2024-3094' } })
  fireEvent.click(screen.getByRole('button', { name: 'Add' }))
  expect(await lastBody(requests, BASE)).toEqual({
    observables: [{ type: 'cve', value: 'cve-2024-3094' }],
  })

  fireEvent.click(screen.getByRole('button', { name: 'Remove evil[.]example' }))
  await vi.waitFor(() => expect(requests.some((r) => r.method === 'DELETE')).toBe(true))
})

test('reports invalid values from the server', async () => {
  mockApi({
    [`POST ${BASE}`]: () =>
      json(
        {
          detail: [
            {
              loc: ['body', 'observables', 0, 'value'],
              msg: 'Value error, not a valid ipv4',
              type: 'value_error',
            },
          ],
        },
        422,
      ),
  })
  render()
  fireEvent.click(screen.getByText('Add observables'))
  fireEvent.change(screen.getByLabelText('Observable value'), { target: { value: '999.1.1.1' } })
  fireEvent.click(screen.getByRole('button', { name: 'Add' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('observables.0.value: not a valid ipv4')
})

test('selects observables for a split', () => {
  const onSelect = render()
  fireEvent.click(screen.getByLabelText('Select observable evil[.]example'))
  expect(onSelect).toHaveBeenCalledWith(['o1'])
})

test('read-only users only see observables', () => {
  render(CASE, false)
  expect(screen.queryByText('Add observables')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /Remove/ })).not.toBeInTheDocument()
})
