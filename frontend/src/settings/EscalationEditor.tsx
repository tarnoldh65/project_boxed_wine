import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type EscalationRule, type Team, type Trigger } from '../api/client'
import { load } from '../api/queries'
import { SaveBar } from './SaveBar'
import { removeAt, splitList, updateAt, useSaveSection } from './editing'

const TRIGGERS: [Trigger, string][] = [
  ['case_created', 'Case created'],
  ['sla_warning', 'SLA warning'],
  ['sla_breach', 'SLA breach'],
  ['major_incident', 'Major incident declared'],
  ['clock_warning', 'Notification deadline approaching'],
  ['clock_breach', 'Notification deadline missed'],
]

type Row = Omit<EscalationRule, 'notify_emails' | 'webhook_url'> & {
  emailsText: string
  webhook: string
}

function toRow({ notify_emails, webhook_url, ...rest }: EscalationRule): Row {
  return { ...rest, emailsText: notify_emails.join(', '), webhook: webhook_url ?? '' }
}

function toRule({ emailsText, webhook, ...rest }: Row): EscalationRule {
  return { ...rest, notify_emails: splitList(emailsText), webhook_url: webhook.trim() || null }
}

export function EscalationEditor({
  slug,
  rules,
  severities,
  teams,
}: {
  slug: string
  rules: EscalationRule[]
  severities: string[]
  teams: Team[]
}) {
  const [draft, setDraft] = useState(() => rules.map(toRow))
  const connections = useQuery({
    queryKey: ['connections', slug],
    queryFn: load('connections', () =>
      api.GET('/api/tenants/{slug}/config/connections', { params: { path: { slug } } }),
    ),
  })
  const slack = connections.data?.filter((c) => c.type === 'slack') ?? []

  function toggleSlack(index: number, name: string, on: boolean) {
    const current = draft[index].slack ?? []
    const next = on ? [...current, name] : current.filter((n) => n !== name)
    setDraft(updateAt(draft, index, { slack: next }))
  }
  const save = useSaveSection(slug, (body: EscalationRule[]) =>
    api.PUT('/api/tenants/{slug}/config/escalation-rules', { params: { path: { slug } }, body }),
  )

  function toggleSeverity(index: number, severity: string, on: boolean) {
    const current = draft[index].severities
    const next = on ? [...current, severity] : current.filter((s) => s !== severity)
    setDraft(updateAt(draft, index, { severities: next }))
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        save.mutate(draft.map(toRule))
      }}
    >
      <h3>Escalation rules</h3>
      <p>Leave every severity unchecked to match all severities.</p>
      {draft.map((rule, i) => (
        <fieldset key={i}>
          <legend>Rule {i + 1}</legend>
          <div className="stack">
            <label>
              Name{' '}
              <input
                aria-label={`Rule ${i + 1} name`}
                value={rule.name}
                onChange={(e) => setDraft(updateAt(draft, i, { name: e.target.value }))}
              />
            </label>
            <label>
              Trigger{' '}
              <select
                aria-label={`Rule ${i + 1} trigger`}
                value={rule.trigger}
                onChange={(e) =>
                  setDraft(updateAt(draft, i, { trigger: e.target.value as Trigger }))
                }
              >
                {TRIGGERS.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <div>
              Severities{' '}
              {severities.map((severity) => (
                <label key={severity}>
                  <input
                    type="checkbox"
                    aria-label={`Rule ${i + 1} ${severity}`}
                    checked={rule.severities.includes(severity)}
                    onChange={(e) => toggleSeverity(i, severity, e.target.checked)}
                  />{' '}
                  {severity}
                </label>
              ))}
            </div>
            <label>
              Notify emails{' '}
              <input
                aria-label={`Rule ${i + 1} emails`}
                placeholder="comma separated"
                value={rule.emailsText}
                onChange={(e) => setDraft(updateAt(draft, i, { emailsText: e.target.value }))}
              />
            </label>
            <label>
              Webhook URL{' '}
              <input
                aria-label={`Rule ${i + 1} webhook`}
                placeholder="https://"
                value={rule.webhook}
                onChange={(e) => setDraft(updateAt(draft, i, { webhook: e.target.value }))}
              />
            </label>
            <div>
              Slack{' '}
              {slack.length === 0 && (
                <span className="note-meta">
                  No Slack connection is set up for this tenant (see docs/integrations.md).
                </span>
              )}
              {slack.map(({ name }) => (
                <label key={name}>
                  <input
                    type="checkbox"
                    aria-label={`Rule ${i + 1} Slack ${name}`}
                    checked={(rule.slack ?? []).includes(name)}
                    onChange={(e) => toggleSlack(i, name, e.target.checked)}
                  />{' '}
                  {name}
                </label>
              ))}
            </div>
            <label>
              Reassign to team{' '}
              <select
                aria-label={`Rule ${i + 1} reassign`}
                value={rule.reassign_to_team ?? ''}
                onChange={(e) =>
                  setDraft(updateAt(draft, i, { reassign_to_team: e.target.value || null }))
                }
              >
                <option value="">No reassignment</option>
                {teams.map((team) => (
                  <option key={team.key} value={team.key}>
                    {team.name}
                  </option>
                ))}
              </select>
            </label>
            <div>
              <button type="button" onClick={() => setDraft(removeAt(draft, i))}>
                Remove rule
              </button>
            </div>
          </div>
        </fieldset>
      ))}
      <button
        type="button"
        onClick={() =>
          setDraft([
            ...draft,
            {
              name: '',
              trigger: 'sla_breach',
              severities: [],
              emailsText: '',
              webhook: '',
              slack: [],
              reassign_to_team: null,
            },
          ])
        }
      >
        Add rule
      </button>
      <SaveBar save={save} />
    </form>
  )
}
