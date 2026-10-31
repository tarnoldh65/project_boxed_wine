import { useState } from 'react'
import { api, type EscalationRule, type Trigger } from '../api/client'
import { SaveBar } from './SaveBar'
import { removeAt, splitList, updateAt, useSaveSection } from './editing'

const TRIGGERS: [Trigger, string][] = [
  ['case_created', 'Case created'],
  ['sla_warning', 'SLA warning'],
  ['sla_breach', 'SLA breach'],
  ['major_incident', 'Major incident declared'],
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
}: {
  slug: string
  rules: EscalationRule[]
  severities: string[]
}) {
  const [draft, setDraft] = useState(() => rules.map(toRow))
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
            { name: '', trigger: 'sla_breach', severities: [], emailsText: '', webhook: '' },
          ])
        }
      >
        Add rule
      </button>
      <SaveBar save={save} />
    </form>
  )
}
