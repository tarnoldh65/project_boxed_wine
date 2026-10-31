import { fireEvent, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import type { Case } from '../api/client'
import { json, mockApi, renderWithQuery } from '../testing'
import { EvidencePanel } from './EvidencePanel'
import { CASE } from './fixtures'

afterEach(() => vi.unstubAllGlobals())

const BASE = '/api/tenants/dr/cases/7/evidence'
const WRITER = ['cases:read', 'cases:write']
const S3_PART = '/bucket/t/e9'

function render(current: Case = CASE, permissions = WRITER) {
  renderWithQuery(<EvidencePanel slug="dr" current={current} permissions={permissions} collector="J Doe" />)
}

function calls(requests: Request[], method: string, path: string) {
  return requests.filter((r) => r.method === method && new URL(r.url).pathname === path)
}

async function waitFor(requests: Request[], method: string, path: string, count = 1) {
  await vi.waitFor(() => expect(calls(requests, method, path).length).toBeGreaterThanOrEqual(count))
  return calls(requests, method, path)
}

function fillCollection() {
  fireEvent.change(screen.getByLabelText('Collected at'), { target: { value: '2026-10-31T09:00' } })
  fireEvent.change(screen.getByLabelText('Collected from'), { target: { value: 'web-01' } })
}

test('lists evidence with downloads only where allowed', () => {
  render()
  expect(screen.getByText('mail.eml')).toBeInTheDocument()
  expect(screen.getByText('2.0 KB')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Download mail.eml' })).toHaveAttribute('href', `${BASE}/e1/download`)
  expect(screen.queryByRole('link', { name: 'Download invoice.exe' })).not.toBeInTheDocument()
})

test('malware handlers can download samples', () => {
  render(CASE, [...WRITER, 'malware:read'])
  expect(screen.getByRole('link', { name: 'Download invoice.exe' })).toBeInTheDocument()
})

test('uploads a file part by part straight to storage', async () => {
  const requests = mockApi({
    [`POST ${BASE}/uploads`]: () =>
      json(
        {
          evidence: { ...CASE.evidence[0], id: 'e9', status: 'uploading' },
          part_size: 4,
          part_urls: [1, 2, 3].map((n) => `http://s3.test${S3_PART}?partNumber=${n}&uploadId=u`),
        },
        201,
      ),
    [`PUT ${S3_PART}`]: () => new Response(null, { status: 200 }),
    [`POST ${BASE}/e9/complete`]: () => json(CASE.evidence[0]),
  })
  render()
  fireEvent.click(screen.getByText('Add evidence', { selector: 'summary' }))
  await userEvent.upload(screen.getByLabelText('File'), new File(['0123456789'], 'capture.pcap'))
  fillCollection()
  fireEvent.click(screen.getByRole('button', { name: 'Add evidence' }))

  const started = (await waitFor(requests, 'POST', `${BASE}/uploads`))[0]
  expect(await started.json()).toEqual({
    name: 'capture.pcap',
    size: 10,
    description: '',
    collection: {
      collected_by: 'J Doe',
      collected_at: new Date('2026-10-31T09:00').toISOString(),
      collected_from: 'web-01',
      method: null,
    },
  })
  const parts = await waitFor(requests, 'PUT', S3_PART, 3)
  expect(await Promise.all(parts.map(async (p) => (await p.text()).length))).toEqual([4, 4, 2])
  await waitFor(requests, 'POST', `${BASE}/e9/complete`)
})

test('abandons the upload when a part fails', async () => {
  const requests = mockApi({
    [`POST ${BASE}/uploads`]: () =>
      json(
        { evidence: { ...CASE.evidence[0], id: 'e9' }, part_size: 4, part_urls: [`http://s3.test${S3_PART}?partNumber=1`] },
        201,
      ),
    [`PUT ${S3_PART}`]: () => new Response('denied', { status: 403 }),
    [`POST ${BASE}/e9/abort`]: () => new Response(null, { status: 204 }),
  })
  render()
  fireEvent.click(screen.getByText('Add evidence', { selector: 'summary' }))
  await userEvent.upload(screen.getByLabelText('File'), new File(['abc'], 'a.bin'))
  fireEvent.click(screen.getByRole('button', { name: 'Add evidence' }))
  await waitFor(requests, 'POST', `${BASE}/e9/abort`)
  expect(await screen.findByRole('alert')).toHaveTextContent('Request failed (403)')
})

test('sends malware samples through the API to be encrypted', async () => {
  const requests = mockApi({ [`POST ${BASE}/malware`]: () => json(CASE.evidence[1], 201) })
  render()
  fireEvent.click(screen.getByText('Add evidence', { selector: 'summary' }))
  fireEvent.click(screen.getByLabelText('Malware sample (stored encrypted)'))
  await userEvent.upload(screen.getByLabelText('File'), new File(['MZ'], 'invoice.exe'))
  fillCollection()
  fireEvent.click(screen.getByRole('button', { name: 'Add evidence' }))

  const form = await (await waitFor(requests, 'POST', `${BASE}/malware`))[0].formData()
  expect((form.get('file') as File).name).toBe('invoice.exe')
  expect(form.get('collected_by')).toBe('J Doe')
  expect(form.get('collected_from')).toBe('web-01')
  expect(form.get('method')).toBeNull()
})

test('registers evidence held elsewhere', async () => {
  const requests = mockApi({ [`POST ${BASE}/references`]: () => json(CASE.evidence[0], 201) })
  render()
  fireEvent.click(screen.getByText('Add evidence', { selector: 'summary' }))
  fireEvent.click(screen.getByLabelText('Held elsewhere'))
  fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Laptop image' } })
  fireEvent.change(screen.getByLabelText('Location'), { target: { value: 'Locker 3' } })
  fireEvent.change(screen.getByLabelText('Size in bytes'), { target: { value: '500' } })
  fillCollection()
  fireEvent.click(screen.getByRole('button', { name: 'Add evidence' }))

  const body = await (await waitFor(requests, 'POST', `${BASE}/references`))[0].json()
  expect(body).toMatchObject({ name: 'Laptop image', location: 'Locker 3', sha256: null, size: 500 })
})

test('shows the chain of custody and records a transfer', async () => {
  const report = {
    evidence: CASE.evidence[0],
    chain_intact: true,
    generated_at: '2026-10-31T12:00:00Z',
    entries: [
      { at: '2026-10-31T10:00:00Z', actor: 'user:u1', action: 'collected', detail: { collected_by: 'J Doe', method: null }, hash: 'h1' },
      { at: '2026-10-31T10:01:00Z', actor: 'user:u1', action: 'uploaded', detail: { size: 2048 }, hash: 'h2' },
    ],
  }
  const requests = mockApi({
    [`GET ${BASE}/e1/custody`]: () => json(report),
    [`POST ${BASE}/e1/transfer`]: () => json(report),
  })
  render()
  fireEvent.click(screen.getByRole('button', { name: 'Custody of mail.eml' }))
  expect(await screen.findByText(/verified intact/)).toBeInTheDocument()
  expect(screen.getByText('collected_by: J Doe')).toBeInTheDocument()

  fireEvent.change(screen.getByLabelText('Transfer to'), { target: { value: 'Counsel' } })
  fireEvent.change(screen.getByLabelText('Transfer reason'), { target: { value: 'Review' } })
  fireEvent.click(screen.getByRole('button', { name: 'Record transfer' }))
  const body = await (await waitFor(requests, 'POST', `${BASE}/e1/transfer`))[0].json()
  expect(body).toEqual({ to: 'Counsel', reason: 'Review', location: null })
})

test('reports a broken custody chain', async () => {
  mockApi({
    [`GET ${BASE}/e1/custody`]: () =>
      json({ evidence: CASE.evidence[0], chain_intact: false, generated_at: '2026-10-31T12:00:00Z', entries: [] }),
  })
  render()
  fireEvent.click(screen.getByRole('button', { name: 'Custody of mail.eml' }))
  expect(await screen.findByText(/BROKEN/)).toBeInTheDocument()
})

test('deletes with a reason unless the case is on legal hold', async () => {
  const requests = mockApi({ [`DELETE ${BASE}/e1`]: () => new Response(null, { status: 204 }) })
  render()
  fireEvent.click(screen.getByRole('button', { name: 'Delete mail.eml' }))
  fireEvent.change(screen.getByLabelText('Reason to delete mail.eml'), { target: { value: 'Duplicate' } })
  fireEvent.click(screen.getByRole('button', { name: 'Confirm delete' }))
  const deleted = (await waitFor(requests, 'DELETE', `${BASE}/e1`))[0]
  expect(new URL(deleted.url).searchParams.get('reason')).toBe('Duplicate')
})

test('legal hold disables deletion', () => {
  render({ ...CASE, legal_hold: true })
  expect(screen.getByRole('button', { name: 'Delete mail.eml' })).toBeDisabled()
})

test('flags integrity failures', () => {
  render({ ...CASE, evidence: [{ ...CASE.evidence[0], status: 'mismatch' }] })
  expect(screen.getByRole('alert')).toHaveTextContent('Integrity check failed')
})

test('read-only users cannot add or delete', () => {
  render(CASE, ['cases:read'])
  expect(screen.queryByText('Add evidence')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /Delete/ })).not.toBeInTheDocument()
})

test('asks for a file before uploading', async () => {
  const requests = mockApi({})
  render()
  fireEvent.click(screen.getByText('Add evidence', { selector: 'summary' }))
  fireEvent.click(screen.getByRole('button', { name: 'Add evidence' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Choose a file to add')
  expect(requests).toEqual([])
})
