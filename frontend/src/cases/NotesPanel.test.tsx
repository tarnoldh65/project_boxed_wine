import { fireEvent, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import type { Case } from '../api/client'
import { json, mockApi, renderWithQuery } from '../testing'
import { CASE, MEMBERS } from './fixtures'
import { NotesPanel } from './NotesPanel'

afterEach(() => vi.unstubAllGlobals())

const BASE = '/api/tenants/dr/cases/7/notes'
const XSS = [
  '<script>window.pwned = true</script>',
  '<img src="x" onerror="window.pwned = true">',
  '[click me](javascript:window.pwned=true)',
  '<a href="javascript:alert(1)">raw link</a>',
].join('\n\n')

function render(current: Case = CASE, canWrite = true) {
  return renderWithQuery(<NotesPanel slug="dr" current={current} members={MEMBERS} canWrite={canWrite} />)
}

async function lastBody(requests: Request[], method: string, path: string) {
  await vi.waitFor(() => expect(requests.some((r) => r.method === method && new URL(r.url).pathname === path)).toBe(true))
  return requests.filter((r) => r.method === method && new URL(r.url).pathname === path).at(-1)!.json()
}

test('renders Markdown with authors and edits', () => {
  render()
  expect(screen.getByRole('heading', { name: 'Findings' })).toBeInTheDocument()
  expect(screen.getByText('clicked').tagName).toBe('STRONG')
  expect(screen.getByText(/J Doe, .*\(edited by A Smith, revision 2\)/)).toBeInTheDocument()
})

test('neutralizes HTML and script links in notes', () => {
  const { container } = render({ ...CASE, notes: [{ ...CASE.notes[0], body: XSS }] })
  expect(container.querySelector('script')).toBeNull()
  expect(container.querySelector('img')).toBeNull()
  expect(container.querySelector('[onerror]')).toBeNull()
  for (const link of container.querySelectorAll('a')) {
    expect(link.getAttribute('href') ?? '').not.toMatch(/^javascript:/i)
  }
  expect(screen.queryByRole('link', { name: 'raw link' })).not.toBeInTheDocument()
  expect((window as { pwned?: boolean }).pwned).toBeUndefined()
})

test('shows the privileged marking on a note', () => {
  render({ ...CASE, notes: [{ ...CASE.notes[0], privileged: true }] })
  expect(screen.getByText('Attorney-Client Privileged / Prepared at Direction of Counsel')).toBeInTheDocument()
})

test('adds a privileged note', async () => {
  const requests = mockApi({ [`POST ${BASE}`]: () => json(CASE, 201) })
  render()
  fireEvent.change(screen.getByLabelText('New note'), { target: { value: 'Call @asmith' } })
  fireEvent.click(screen.getByLabelText('Privileged'))
  fireEvent.click(screen.getByRole('button', { name: 'Add note' }))
  expect(await lastBody(requests, 'POST', BASE)).toEqual({ body: 'Call @asmith', privileged: true })
})

test('edits a note', async () => {
  const requests = mockApi({ [`PATCH ${BASE}/n1`]: () => json(CASE) })
  render()
  fireEvent.click(screen.getByRole('button', { name: 'Edit note' }))
  expect(screen.getByLabelText('Edit note')).toHaveValue('## Findings\n\nUser **clicked** the link.')
  fireEvent.change(screen.getByLabelText('Edit note'), { target: { value: 'Updated' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save note' }))
  expect(await lastBody(requests, 'PATCH', `${BASE}/n1`)).toEqual({ body: 'Updated', privileged: false })
  await vi.waitFor(() => expect(screen.queryByLabelText('Edit note')).not.toBeInTheDocument())
})

test('shows the revision history as source text', async () => {
  mockApi({
    [`GET ${BASE}/n1/revisions`]: () =>
      json([
        { revision: 1, body: 'Draft <b>bold</b>', privileged: false, edited_by: 'user:u1', edited_at: '2026-10-31T10:00:00Z' },
        { revision: 2, body: 'Final', privileged: true, edited_by: 'user:u2', edited_at: '2026-10-31T11:00:00Z' },
      ]),
  })
  render()
  fireEvent.click(screen.getByRole('button', { name: 'History' }))
  expect(await screen.findByText(/Revision 1 by J Doe/)).toBeInTheDocument()
  expect(screen.getByText(/Revision 2 by A Smith, .*\(privileged\)/)).toBeInTheDocument()
  expect(screen.getByText('Draft <b>bold</b>').tagName).toBe('PRE')
})

test('read-only users cannot add or edit notes', () => {
  render(CASE, false)
  expect(screen.queryByLabelText('New note')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Edit note' })).not.toBeInTheDocument()
})
