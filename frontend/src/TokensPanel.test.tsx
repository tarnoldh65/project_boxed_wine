import { fireEvent, screen, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import type { Token } from './api/client'
import { TokensPanel } from './TokensPanel'
import { json, mockApi, renderWithQuery } from './testing'

const URL_PATH = '/api/tenants/detect-respond/tokens'
const FUTURE = '2099-01-01T00:00:00Z'

function token(overrides: Partial<Token>): Token {
  return {
    id: 'id-1',
    name: 'siem',
    prefix: 'cmt_abcdefgh',
    scopes: ['cases:read'],
    created_at: '2026-01-01T00:00:00Z',
    expires_at: FUTURE,
    revoked_at: null,
    ...overrides,
  }
}

afterEach(() => vi.unstubAllGlobals())

test('lists tokens with their status', async () => {
  mockApi({
    [`GET ${URL_PATH}`]: () =>
      json([
        token({ id: 'a', name: 'active' }),
        token({ id: 'r', name: 'revoked', revoked_at: '2026-02-01T00:00:00Z' }),
        token({ id: 'e', name: 'expired', expires_at: '2020-01-01T00:00:00Z' }),
      ]),
  })
  renderWithQuery(<TokensPanel slug="detect-respond" />)
  const rows = (await screen.findAllByRole('row')).slice(1)
  expect(rows.map((r) => within(r).getAllByRole('cell')[4].textContent)).toEqual([
    'Active',
    'Revoked',
    'Expired',
  ])
  expect(screen.getAllByRole('button', { name: /Revoke/ })).toHaveLength(1)
})

test('creates a token and shows the secret once', async () => {
  let tokens: Token[] = []
  const requests = mockApi({
    [`GET ${URL_PATH}`]: () => json(tokens),
    [`POST ${URL_PATH}`]: () => {
      tokens = [token({ name: 'soar' })]
      return json({ ...tokens[0], token: 'cmt_secret' }, 201)
    },
  })
  renderWithQuery(<TokensPanel slug="detect-respond" />)

  fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'soar' } })
  fireEvent.click(screen.getByLabelText('cases:write'))
  fireEvent.change(screen.getByLabelText('Expires in days'), { target: { value: '30' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create token' }))

  expect(await screen.findByRole('status')).toHaveTextContent('cmt_secret')
  expect(await screen.findByRole('cell', { name: 'soar' })).toBeInTheDocument()
  const post = requests.find((r) => r.method === 'POST')!
  expect(await post.json()).toEqual({
    name: 'soar',
    scopes: ['cases:read', 'cases:write'],
    expires_in_days: 30,
  })
})

test('reports a failed create', async () => {
  mockApi({
    [`GET ${URL_PATH}`]: () => json([]),
    [`POST ${URL_PATH}`]: () => json({ detail: [] }, 422),
  })
  renderWithQuery(<TokensPanel slug="detect-respond" />)
  fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'x' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create token' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Could not create token (422)')
})

test('revokes a token', async () => {
  const requests = mockApi({
    [`GET ${URL_PATH}`]: () => json([token({ id: 'id-9', name: 'siem' })]),
    [`DELETE ${URL_PATH}/id-9`]: () => new Response(null, { status: 204 }),
  })
  renderWithQuery(<TokensPanel slug="detect-respond" />)
  fireEvent.click(await screen.findByRole('button', { name: 'Revoke siem' }))
  await vi.waitFor(() => expect(requests.some((r) => r.method === 'DELETE')).toBe(true))
})

test('reports a failed load', async () => {
  mockApi({ [`GET ${URL_PATH}`]: () => json({ detail: 'Forbidden' }, 403) })
  renderWithQuery(<TokensPanel slug="detect-respond" />)
  expect(await screen.findByRole('alert')).toHaveTextContent('Could not load tokens (403)')
})
