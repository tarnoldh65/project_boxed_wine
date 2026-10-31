import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { MemoryRouter, Outlet, Route, Routes, useLocation } from 'react-router'
import { vi } from 'vitest'
import type { TenantAccess } from './api/client'

type Route = (request: Request) => Response | Promise<Response>

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** Stub fetch with handlers keyed by "METHOD /path"; returns the requests received. */
export function mockApi(routes: Record<string, Route>): Request[] {
  const requests: Request[] = []
  vi.stubGlobal('fetch', async (request: Request) => {
    requests.push(request.clone())
    const route = routes[`${request.method} ${new URL(request.url).pathname}`]
    return route ? route(request) : json({ detail: 'Not Found' }, 404)
  })
  return requests
}

export function renderWithQuery(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

function Location() {
  return <p>Now at {useLocation().pathname}</p>
}

/** Render a tenant page at `route` under /t/:slug; other paths show where navigation went. */
export function renderInTenant(
  element: ReactElement,
  tenant: TenantAccess,
  { path = `/t/${tenant.slug}`, route = '' } = {},
) {
  return renderWithQuery(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/t/:slug" element={<Outlet context={tenant} />}>
          {route ? <Route path={route} element={element} /> : <Route index element={element} />}
        </Route>
        <Route path="*" element={<Location />} />
      </Routes>
    </MemoryRouter>,
  )
}
