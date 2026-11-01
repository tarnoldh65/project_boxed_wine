import { fireEvent, screen, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import type { Case } from '../api/client'
import { json, mockApi, renderWithQuery } from '../testing'
import { CASE, MEMBERS } from './fixtures'
import { RegulatoryPanel } from './RegulatoryPanel'

afterEach(() => vi.unstubAllGlobals())

const BASE = '/api/tenants/dr/cases/7'

function render(current: Case = CASE, { canWrite = true, canDecide = true } = {}) {
  renderWithQuery(
    <RegulatoryPanel slug="dr" current={current} members={MEMBERS} canWrite={canWrite} canDecide={canDecide} />,
  )
}

async function lastBody(requests: Request[], path: string) {
  await vi.waitFor(() => expect(requests.some((r) => new URL(r.url).pathname === path)).toBe(true))
  return requests.filter((r) => new URL(r.url).pathname === path).at(-1)!.json()
}

test('shows clocks, decisions, and the notification log', () => {
  render()
  const clocks = within(screen.getByRole('table', { name: 'Notification clocks' }))
  const [gdpr, state] = clocks.getAllByRole('row').slice(1)
  expect(within(gdpr).getByText('GDPR Art. 33')).toBeInTheDocument()
  expect(within(gdpr).getByText('pending')).toHaveClass('sla-pending')
  expect(within(state).getByText(/^met /)).toHaveClass('sla-met')

  expect(screen.getByText('Not material', { selector: 'strong' })).toBeInTheDocument()
  expect(screen.getByText(/decided by General counsel .*No customer data involved/)).toBeInTheDocument()
  expect(screen.getByText('(recorded by A Smith)')).toBeInTheDocument()

  const logged = screen.getByText('Complaint I3-42').closest('tr')!
  expect(within(logged).getByText('FBI IC3 (law enforcement)')).toBeInTheDocument()
  expect(within(logged).getByText('US state breach law')).toBeInTheDocument()
  expect(within(logged).getByText('mail.eml')).toBeInTheDocument()
  expect(within(logged).getByText('J Doe')).toBeInTheDocument()
})

test('explains when no clock is running', () => {
  render({ ...CASE, clocks: [], materiality: [], disclosures: [] })
  expect(screen.getByText(/No notification clocks/)).toBeInTheDocument()
  expect(screen.getByText('No decision recorded.')).toBeInTheDocument()
  expect(screen.getByText('No notifications sent.')).toBeInTheDocument()
})

test('records a materiality decision', async () => {
  const requests = mockApi({ [`POST ${BASE}/materiality`]: () => json(CASE, 201) })
  render()
  fireEvent.change(screen.getByLabelText('Decided by'), { target: { value: 'Disclosure committee' } })
  fireEvent.change(screen.getByLabelText('Decision time'), { target: { value: '2026-10-31T09:00' } })
  fireEvent.change(screen.getByLabelText('Rationale'), { target: { value: 'Customer data taken' } })
  fireEvent.click(screen.getByRole('button', { name: 'Record decision' }))

  expect(await lastBody(requests, `${BASE}/materiality`)).toEqual({
    material: true,
    decided_by: 'Disclosure committee',
    decided_at: new Date('2026-10-31T09:00').toISOString(),
    rationale: 'Customer data taken',
  })
})

test('logs a notification that meets a clock, with attachments', async () => {
  const requests = mockApi({ [`POST ${BASE}/disclosures`]: () => json({ detail: [{ loc: ['body'], msg: 'Value error, the clock must belong to this case', type: 'value_error' }] }, 422) })
  render()
  fireEvent.change(screen.getByLabelText('Recipient type'), { target: { value: 'regulator' } })
  fireEvent.change(screen.getByLabelText('Recipient'), { target: { value: 'ICO' } })
  fireEvent.change(screen.getByLabelText('Sent at'), { target: { value: '2026-11-01T08:00' } })
  fireEvent.change(screen.getByLabelText('Notification summary'), { target: { value: 'Initial report' } })
  const options = within(screen.getByLabelText('Meets clock')).getAllByRole('option').map((o) => o.textContent)
  expect(options).toEqual(['Meets no clock', 'GDPR Art. 33']) // met clocks are not offered
  fireEvent.change(screen.getByLabelText('Meets clock'), { target: { value: 'k1' } })
  fireEvent.click(screen.getByLabelText('invoice.exe'))
  fireEvent.click(screen.getByRole('button', { name: 'Log notification' }))

  expect(await lastBody(requests, `${BASE}/disclosures`)).toEqual({
    recipient_type: 'regulator',
    recipient: 'ICO',
    sent_at: new Date('2026-11-01T08:00').toISOString(),
    summary: 'Initial report',
    clock_id: 'k1',
    evidence_ids: ['e2'],
  })
  expect(await screen.findByRole('alert')).toHaveTextContent('the clock must belong to this case')
})

test('a notification can meet no clock and carry no attachments', async () => {
  const requests = mockApi({ [`POST ${BASE}/disclosures`]: () => json(CASE, 201) })
  render({ ...CASE, evidence: [] })
  expect(screen.queryByRole('group', { name: 'Attachments' })).not.toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Recipient'), { target: { value: 'Carrier' } })
  fireEvent.change(screen.getByLabelText('Sent at'), { target: { value: '2026-11-01T08:00' } })
  fireEvent.change(screen.getByLabelText('Notification summary'), { target: { value: 'Claim' } })
  fireEvent.click(screen.getByRole('button', { name: 'Log notification' }))

  expect(await lastBody(requests, `${BASE}/disclosures`)).toMatchObject({
    recipient_type: 'regulator',
    clock_id: null,
    evidence_ids: [],
  })
})

test('only legal managers record decisions; read-only users log nothing', () => {
  render(CASE, { canWrite: true, canDecide: false })
  expect(screen.queryByRole('button', { name: 'Record decision' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Log notification' })).toBeInTheDocument()
})

test('read-only users see the record without forms', () => {
  render(CASE, { canWrite: false, canDecide: false })
  expect(screen.queryByRole('button', { name: 'Log notification' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Record decision' })).not.toBeInTheDocument()
})
