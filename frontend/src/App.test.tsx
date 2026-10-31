import { fireEvent, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import App from './App'
import type { Me } from './api/client'
import { json, mockApi, renderWithQuery } from './testing'

const ME: Me = {
  id: '00000000-0000-0000-0000-000000000001',
  username: 'jdoe',
  display_name: 'J Doe',
  email: 'jdoe@example.test',
  is_platform_admin: false,
  tenants: [
    { slug: 'detect-respond', name: 'Detect and Respond', permissions: ['cases:read'] },
    { slug: 'insider-threat', name: 'Insider Threat', permissions: ['cases:read'] },
  ],
}

afterEach(() => {
  vi.unstubAllGlobals()
  localStorage.clear()
})

test('offers sign in when there is no session', async () => {
  mockApi({ 'GET /api/me': () => json({ detail: 'Not authenticated' }, 401) })
  renderWithQuery(<App />)
  expect(await screen.findByRole('link', { name: 'Sign in' })).toHaveAttribute(
    'href',
    '/api/auth/login',
  )
})

test('shows the user and their first tenant', async () => {
  mockApi({ 'GET /api/me': () => json(ME) })
  renderWithQuery(<App />)
  expect(await screen.findByRole('heading', { name: 'Detect and Respond' })).toBeInTheDocument()
  expect(screen.getByText('J Doe')).toBeInTheDocument()
  expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual([
    'Detect and Respond',
    'Insider Threat',
  ])
  expect(screen.queryByRole('heading', { name: 'API tokens' })).not.toBeInTheDocument()
})

test('switches tenant and remembers the choice', async () => {
  mockApi({ 'GET /api/me': () => json(ME) })
  renderWithQuery(<App />)
  fireEvent.change(await screen.findByLabelText('Tenant'), {
    target: { value: 'insider-threat' },
  })
  expect(screen.getByRole('heading', { name: 'Insider Threat' })).toBeInTheDocument()
  expect(localStorage.getItem('casemgr.tenant')).toBe('insider-threat')
})

test('ignores a remembered tenant the user no longer belongs to', async () => {
  localStorage.setItem('casemgr.tenant', 'revoked-tenant')
  mockApi({ 'GET /api/me': () => json(ME) })
  renderWithQuery(<App />)
  expect(await screen.findByRole('heading', { name: 'Detect and Respond' })).toBeInTheDocument()
})

test('shows token management to tenant admins', async () => {
  const admin = {
    ...ME,
    tenants: [{ ...ME.tenants[0], permissions: ['cases:read', 'tokens:manage'] }],
  }
  mockApi({
    'GET /api/me': () => json(admin),
    'GET /api/tenants/detect-respond/tokens': () => json([]),
  })
  renderWithQuery(<App />)
  fireEvent.click(await screen.findByRole('button', { name: 'Settings' }))
  expect(await screen.findByRole('heading', { name: 'API tokens' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Cases' }))
  expect(screen.getByRole('heading', { name: 'Cases' })).toBeInTheDocument()
})

test('hides settings from non-admins', async () => {
  mockApi({ 'GET /api/me': () => json(ME) })
  renderWithQuery(<App />)
  expect(await screen.findByRole('heading', { name: 'Cases' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Settings' })).not.toBeInTheDocument()
})

test('explains when the user has no tenants', async () => {
  mockApi({ 'GET /api/me': () => json({ ...ME, tenants: [] }) })
  renderWithQuery(<App />)
  expect(await screen.findByText(/do not have access to any tenant/)).toBeInTheDocument()
  expect(screen.queryByLabelText('Tenant')).not.toBeInTheDocument()
})

test('reports a server error', async () => {
  mockApi({ 'GET /api/me': () => json({ detail: 'boom' }, 500) })
  renderWithQuery(<App />)
  expect(await screen.findByRole('alert')).toHaveTextContent('Could not load your profile (500)')
})

test('signs out through the API', async () => {
  const requests = mockApi({
    'GET /api/me': () => json(ME),
    'POST /api/auth/logout': () => json({ logout_url: '/' }),
  })
  renderWithQuery(<App />)
  fireEvent.click(await screen.findByRole('button', { name: 'Sign out' }))
  await vi.waitFor(() =>
    expect(requests.map((r) => `${r.method} ${new URL(r.url).pathname}`)).toContain(
      'POST /api/auth/logout',
    ),
  )
})
