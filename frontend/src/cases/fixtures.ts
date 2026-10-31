import type { Case, Member, TenantAccess, TenantConfig } from '../api/client'

const SLA = { acknowledge_minutes: 15, triage_minutes: 60, contain_minutes: 240, resolve_minutes: 1440 }

export const TENANT: TenantAccess = {
  slug: 'dr',
  name: 'DR',
  permissions: ['cases:read', 'cases:write'],
}

export const MEMBERS: Member[] = [
  { id: 'u1', username: 'jdoe', display_name: 'J Doe' },
  { id: 'u2', username: 'asmith', display_name: 'A Smith' },
]

export const CONFIG: TenantConfig = {
  states: [
    { key: 'new', name: 'New', terminal: false, require_tasks_done: false },
    { key: 'triage', name: 'Triage', terminal: false, sla_milestone: 'triage', require_tasks_done: false },
    { key: 'closed', name: 'Closed', terminal: true, require_tasks_done: true },
  ],
  custom_fields: [
    { key: 'vector', label: 'Vector', type: 'picklist', options: ['email', 'web'] },
    { key: 'handler', label: 'Handler', type: 'user', options: [] },
    { key: 'records', label: 'Records', type: 'number', options: [] },
  ],
  severity: {
    levels: [
      { key: 'high', sla: SLA },
      { key: 'low', sla: SLA },
    ],
    impacts: ['high', 'low'],
    urgencies: ['high', 'low'],
    matrix: [
      ['high', 'high'],
      ['high', 'low'],
    ],
  },
  escalation_rules: [],
  teams: [{ key: 'soc', name: 'SOC', on_call: 'u1' }],
  templates: [
    { key: 'phishing', name: 'Phishing', impact: 'low', urgency: 'high', tlp: 'amber', pap: 'amber', team: 'soc', tasks: [] },
    { key: 'bec', name: 'BEC', impact: 'high', urgency: 'low', tlp: 'amber', pap: 'amber', team: null, tasks: [] },
  ],
}

export const CASE: Case = {
  id: 'c7',
  number: 7,
  title: 'Suspicious email',
  description: '',
  state: 'new',
  category: 'phishing',
  impact: 'low',
  urgency: 'high',
  severity: 'high',
  priority: 'P1',
  override_justification: null,
  team: 'soc',
  owner_id: 'u1',
  source: 'email',
  tlp: 'amber',
  pap: 'amber',
  restricted: false,
  regulated_data: [],
  data_regions: [],
  business_units: [],
  attack_techniques: [],
  custom: {},
  resolution: null,
  merged_into: null,
  detected_at: null,
  closed_at: null,
  acknowledged_at: null,
  triaged_at: null,
  contained_at: null,
  major_incident_at: null,
  major_incident_reason: null,
  sla_status: 'pending',
  sla: [
    { step: 'acknowledge', due_at: '2026-10-31T10:30:00Z', met_at: null, status: 'warning' },
    { step: 'triage', due_at: '2026-10-31T14:00:00Z', met_at: null, status: 'pending' },
  ],
  tasks: [
    {
      id: 't1',
      title: 'Identify recipients',
      description: '',
      status: 'open',
      assignee_id: null,
      due_at: '2026-10-31T12:00:00Z',
      depends_on: [],
      checklist: [{ text: 'Query mail logs', done: false }],
      waive_reason: null,
      playbook_key: 'recipients',
      blocked: false,
      created_at: '2026-10-31T10:00:00Z',
      completed_at: null,
    },
    {
      id: 't2',
      title: 'Reset credentials',
      description: '',
      status: 'open',
      assignee_id: null,
      due_at: null,
      depends_on: ['t1'],
      checklist: [],
      waive_reason: null,
      playbook_key: 'reset',
      blocked: true,
      created_at: '2026-10-31T10:00:00Z',
      completed_at: null,
    },
  ],
  created_at: '2026-10-31T10:00:00Z',
  updated_at: '2026-10-31T10:00:00Z',
  alerts: [
    {
      id: 'a1',
      source: 'email',
      external_id: '<m1@example.test>',
      group_key: null,
      title: 'Invoice overdue',
      payload: { from: 'billing@evil.example' },
      received_at: '2026-10-31T10:00:00Z',
    },
  ],
  assets: [{ id: 'h1', kind: 'host', name: 'web-01', external_id: 'CI-1' }],
  observables: [
    {
      id: 'o1',
      type: 'domain',
      value: 'evil.example',
      defanged: 'evil[.]example',
      other_cases: 2,
    },
  ],
}
