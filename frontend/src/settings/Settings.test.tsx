import { fireEvent, screen, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import type { TenantAccess, TenantConfig } from '../api/client'
import { json, mockApi, renderInTenant } from '../testing'
import { Settings } from './Settings'

const BASE = '/api/tenants/dr/config'
const SLA = { acknowledge_minutes: 15, triage_minutes: 60, contain_minutes: 240, resolve_minutes: 1440 }
const CONFIG: TenantConfig = {
  states: [
    { key: 'new', name: 'New', terminal: false },
    { key: 'closed', name: 'Closed', terminal: true },
  ],
  custom_fields: [],
  severity: {
    levels: [
      { key: 'high', sla: SLA },
      { key: 'low', sla: SLA },
    ],
    impacts: ['wide', 'narrow'],
    urgencies: ['now'],
    matrix: [['high'], ['low']],
  },
  escalation_rules: [],
  teams: [],
  templates: [{ key: 'phishing', name: 'Phishing', impact: 'wide', urgency: 'now', tlp: 'amber', pap: 'amber', team: null }],
}
const MEMBERS = [{ id: 'u1', username: 'jdoe', display_name: 'J Doe' }]
const ADMIN: TenantAccess = { slug: 'dr', name: 'DR', permissions: ['cases:read', 'config:manage'] }

afterEach(() => vi.unstubAllGlobals())

/** Mock the config API; each PUT echoes its body back into the saved configuration. */
function mockConfig() {
  const sections: Record<string, keyof TenantConfig> = {
    states: 'states',
    'custom-fields': 'custom_fields',
    severity: 'severity',
    'escalation-rules': 'escalation_rules',
    teams: 'teams',
    templates: 'templates',
  }
  let config = CONFIG
  const routes = Object.fromEntries(
    Object.entries(sections).map(([path, key]) => [
      `PUT ${BASE}/${path}`,
      async (request: Request) => {
        config = { ...config, [key]: await request.json() }
        return json(config)
      },
    ]),
  )
  return mockApi({
    [`GET ${BASE}`]: () => json(config),
    'GET /api/tenants/dr/members': () => json(MEMBERS),
    ...routes,
  })
}

async function lastBody(requests: Request[], path: string) {
  await vi.waitFor(() => expect(requests.some((r) => r.url.endsWith(path))).toBe(true))
  return requests.filter((r) => r.url.endsWith(path)).at(-1)!.json()
}

function section(heading: string) {
  return within(screen.getByRole('heading', { name: heading }).closest('form')!)
}

test('loads every section for configuration admins', async () => {
  mockConfig()
  renderInTenant(<Settings />, ADMIN)
  for (const heading of [
    'Lifecycle states',
    'Custom fields',
    'Severity and SLAs',
    'Escalation rules',
    'Teams',
    'Case templates',
  ]) {
    expect(await screen.findByRole('heading', { name: heading })).toBeInTheDocument()
  }
  expect(screen.queryByRole('heading', { name: 'API tokens' })).not.toBeInTheDocument()
})

test('token-only admins see just API tokens', async () => {
  const requests = mockApi({
    'GET /api/tenants/dr/tokens': () => json([]),
    'GET /api/tenants/dr/members': () => json(MEMBERS),
  })
  renderInTenant(<Settings />, { ...ADMIN, permissions: ['tokens:manage'] })
  expect(await screen.findByRole('heading', { name: 'API tokens' })).toBeInTheDocument()
  expect(requests.map((r) => new URL(r.url).pathname)).not.toContain(BASE)
})

test('edits, reorders, and saves lifecycle states', async () => {
  const requests = mockConfig()
  renderInTenant(<Settings />, ADMIN)
  await screen.findByRole('heading', { name: 'Lifecycle states' })
  const states = section('Lifecycle states')

  fireEvent.click(states.getByRole('button', { name: 'Add state' }))
  fireEvent.change(states.getByLabelText('State 3 key'), { target: { value: 'triage' } })
  fireEvent.change(states.getByLabelText('State 3 name'), { target: { value: 'Triage' } })
  fireEvent.click(states.getAllByRole('button', { name: 'Up' })[2])
  fireEvent.change(states.getByLabelText('State 1 name'), { target: { value: 'Open' } })
  fireEvent.click(states.getByRole('button', { name: 'Save' }))

  expect(await lastBody(requests, '/states')).toEqual([
    { key: 'new', name: 'Open', terminal: false },
    { key: 'triage', name: 'Triage', terminal: false },
    { key: 'closed', name: 'Closed', terminal: true },
  ])
  expect(await states.findByRole('status')).toHaveTextContent('Saved')
})

test('shows validation errors from the server', async () => {
  mockApi({
    [`GET ${BASE}`]: () => json(CONFIG),
    'GET /api/tenants/dr/members': () => json(MEMBERS),
    [`PUT ${BASE}/states`]: () =>
      json(
        {
          detail: [
            { loc: [], msg: 'Value error, at least one state must be terminal', type: 'value_error' },
            { loc: ['body', 0, 'key'], msg: 'String should match pattern', type: 'string_pattern_mismatch' },
          ],
        },
        422,
      ),
  })
  renderInTenant(<Settings />, ADMIN)
  await screen.findByRole('heading', { name: 'Lifecycle states' })
  const states = section('Lifecycle states')
  fireEvent.click(states.getByLabelText('State 2 terminal'))
  fireEvent.click(states.getByRole('button', { name: 'Save' }))
  expect(await states.findByRole('alert')).toHaveTextContent(
    'at least one state must be terminal; 0.key: String should match pattern',
  )
})

test('adds a picklist field required from a state', async () => {
  const requests = mockConfig()
  renderInTenant(<Settings />, ADMIN)
  await screen.findByRole('heading', { name: 'Custom fields' })
  const fields = section('Custom fields')

  fireEvent.click(fields.getByRole('button', { name: 'Add field' }))
  fireEvent.change(fields.getByLabelText('Field 1 key'), { target: { value: 'vector' } })
  fireEvent.change(fields.getByLabelText('Field 1 label'), { target: { value: 'Vector' } })
  expect(fields.queryByLabelText('Field 1 options')).not.toBeInTheDocument()
  fireEvent.change(fields.getByLabelText('Field 1 type'), { target: { value: 'picklist' } })
  fireEvent.change(fields.getByLabelText('Field 1 options'), { target: { value: 'email, web,, ' } })
  fireEvent.change(fields.getByLabelText('Field 1 required from'), { target: { value: 'closed' } })
  fireEvent.click(fields.getByRole('button', { name: 'Save' }))

  expect(await lastBody(requests, '/custom-fields')).toEqual([
    { key: 'vector', label: 'Vector', type: 'picklist', options: ['email', 'web'], required_from: 'closed' },
  ])
})

test('edits SLAs and the severity matrix, resizing it when axes change', async () => {
  const requests = mockConfig()
  renderInTenant(<Settings />, ADMIN)
  await screen.findByRole('heading', { name: 'Severity and SLAs' })
  const severity = section('Severity and SLAs')

  fireEvent.change(severity.getByLabelText('Level 2 resolve minutes'), { target: { value: '2880' } })
  fireEvent.change(severity.getByLabelText('Severity for narrow impact, now urgency'), {
    target: { value: 'high' },
  })
  fireEvent.change(severity.getByLabelText('Urgencies'), { target: { value: 'now, later' } })
  expect(severity.getByLabelText('Severity for wide impact, later urgency')).toHaveValue('low')
  fireEvent.click(severity.getByRole('button', { name: 'Save' }))

  expect(await lastBody(requests, '/severity')).toEqual({
    levels: [
      { key: 'high', sla: SLA },
      { key: 'low', sla: { ...SLA, resolve_minutes: 2880 } },
    ],
    impacts: ['wide', 'narrow'],
    urgencies: ['now', 'later'],
    matrix: [
      ['high', 'low'],
      ['high', 'low'],
    ],
  })
})

test('adds and removes escalation rules', async () => {
  const requests = mockConfig()
  renderInTenant(<Settings />, ADMIN)
  await screen.findByRole('heading', { name: 'Escalation rules' })
  const rules = section('Escalation rules')

  fireEvent.click(rules.getByRole('button', { name: 'Add rule' }))
  fireEvent.click(rules.getByRole('button', { name: 'Add rule' }))
  fireEvent.change(rules.getByLabelText('Rule 1 name'), { target: { value: 'Page IR' } })
  fireEvent.change(rules.getByLabelText('Rule 1 trigger'), { target: { value: 'major_incident' } })
  fireEvent.click(rules.getByLabelText('Rule 1 high'))
  fireEvent.click(rules.getByLabelText('Rule 1 low'))
  fireEvent.click(rules.getByLabelText('Rule 1 low'))
  fireEvent.change(rules.getByLabelText('Rule 1 emails'), {
    target: { value: 'ir@example.com, lead@example.com' },
  })
  fireEvent.change(rules.getByLabelText('Rule 1 webhook'), { target: { value: ' https://hooks.example.com/x ' } })
  fireEvent.click(rules.getAllByRole('button', { name: 'Remove rule' })[1])
  fireEvent.click(rules.getByRole('button', { name: 'Save' }))

  expect(await lastBody(requests, '/escalation-rules')).toEqual([
    {
      name: 'Page IR',
      trigger: 'major_incident',
      severities: ['high'],
      notify_emails: ['ir@example.com', 'lead@example.com'],
      webhook_url: 'https://hooks.example.com/x',
    },
  ])
})

test('reports a failed load', async () => {
  mockApi({ [`GET ${BASE}`]: () => json({ detail: 'Forbidden' }, 403) })
  renderInTenant(<Settings />, ADMIN)
  expect(await screen.findByRole('alert')).toHaveTextContent('Could not load settings (403)')
})

test('assigns an on-call member to a team', async () => {
  const requests = mockConfig()
  renderInTenant(<Settings />, ADMIN)
  await screen.findByRole('heading', { name: 'Teams' })
  const teams = section('Teams')

  fireEvent.click(teams.getByRole('button', { name: 'Add team' }))
  fireEvent.change(teams.getByLabelText('Team 1 key'), { target: { value: 'soc' } })
  fireEvent.change(teams.getByLabelText('Team 1 name'), { target: { value: 'SOC' } })
  fireEvent.change(teams.getByLabelText('Team 1 on call'), { target: { value: 'u1' } })
  fireEvent.click(teams.getByRole('button', { name: 'Save' }))

  expect(await lastBody(requests, '/teams')).toEqual([{ key: 'soc', name: 'SOC', on_call: 'u1' }])
})

test('edits case templates', async () => {
  const requests = mockConfig()
  renderInTenant(<Settings />, ADMIN)
  await screen.findByRole('heading', { name: 'Case templates' })
  const templates = section('Case templates')

  fireEvent.change(templates.getByLabelText('Template 1 impact'), { target: { value: 'narrow' } })
  fireEvent.change(templates.getByLabelText('Template 1 TLP'), { target: { value: 'red' } })
  fireEvent.click(templates.getByRole('button', { name: 'Add template' }))
  fireEvent.change(templates.getByLabelText('Template 2 key'), { target: { value: 'ddos' } })
  fireEvent.change(templates.getByLabelText('Template 2 name'), { target: { value: 'DDoS' } })
  fireEvent.click(templates.getByRole('button', { name: 'Save' }))

  expect(await lastBody(requests, '/templates')).toEqual([
    { key: 'phishing', name: 'Phishing', impact: 'narrow', urgency: 'now', tlp: 'red', pap: 'amber', team: null },
    { key: 'ddos', name: 'DDoS', impact: 'wide', urgency: 'now', tlp: 'amber', pap: 'amber', team: null },
  ])
})
