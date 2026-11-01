import type { FormEvent } from 'react'
import { api, type Case, type Member, type Recipient } from '../api/client'
import { actorName, useCaseAction } from './journal'

const RECIPIENTS: [Recipient, string][] = [
  ['regulator', 'Regulator'],
  ['law_enforcement', 'Law enforcement'],
  ['insurer', 'Insurer'],
  ['customer', 'Customer'],
]

const when = (value: string) => new Date(value).toLocaleString()
const iso = (form: FormData, name: string) => new Date(String(form.get(name))).toISOString()

export function RegulatoryPanel({
  slug,
  current,
  members,
  canWrite,
  canDecide,
}: {
  slug: string
  current: Case
  members: Member[]
  canWrite: boolean
  canDecide: boolean
}) {
  const action = useCaseAction(slug, current.number)
  const path = { params: { path: { slug, number: current.number } } }
  const clockName = (id: string | null) => current.clocks.find((c) => c.id === id)?.name
  const evidenceName = (id: string) => current.evidence.find((e) => e.id === id)?.name ?? 'Removed evidence'
  const attachable = current.evidence.filter((e) => e.status !== 'deleted')

  function decide(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    event.currentTarget.reset()
    action.mutate(() =>
      api.POST('/api/tenants/{slug}/cases/{number}/materiality', {
        ...path,
        body: {
          material: form.get('material') === 'yes',
          decided_by: String(form.get('decided_by')),
          decided_at: iso(form, 'decided_at'),
          rationale: String(form.get('rationale')),
        },
      }),
    )
  }

  function log(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    event.currentTarget.reset()
    action.mutate(() =>
      api.POST('/api/tenants/{slug}/cases/{number}/disclosures', {
        ...path,
        body: {
          recipient_type: form.get('recipient_type') as Recipient,
          recipient: String(form.get('recipient')),
          sent_at: iso(form, 'sent_at'),
          summary: String(form.get('summary')),
          clock_id: String(form.get('clock_id')) || null,
          evidence_ids: form.getAll('evidence_ids').map(String),
        },
      }),
    )
  }

  return (
    <section>
      <h3>Regulatory notifications</h3>
      {current.clocks.length === 0 && (
        <p>No notification clocks. They start when regulated data is flagged or an incident is found material.</p>
      )}
      {current.clocks.length > 0 && (
        <table>
          <caption>Notification clocks</caption>
          <thead>
            <tr>
              <th>Obligation</th>
              <th>Started</th>
              <th>Due</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {current.clocks.map((clock) => (
              <tr key={clock.id}>
                <td>{clock.name}</td>
                <td>{when(clock.started_at)}</td>
                <td>{when(clock.due_at)}</td>
                <td className={`sla-${clock.status}`}>
                  {clock.status}
                  {clock.met_at && ` ${when(clock.met_at)}`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h4>Materiality decisions</h4>
      {current.materiality.length === 0 && <p>No decision recorded.</p>}
      <ul>
        {current.materiality.map((d) => (
          <li key={d.id}>
            <strong>{d.material ? 'Material' : 'Not material'}</strong>, decided by {d.decided_by} on{' '}
            {when(d.decided_at)}: {d.rationale}{' '}
            <span className="note-meta">(recorded by {actorName(d.recorded_by, members)})</span>
          </li>
        ))}
      </ul>
      {canDecide && (
        <form className="stack" onSubmit={decide}>
          <select name="material" aria-label="Materiality" defaultValue="yes">
            <option value="yes">Material or reportable</option>
            <option value="no">Not material</option>
          </select>
          <input name="decided_by" required maxLength={200} aria-label="Decided by" placeholder="Decision maker" />
          <input name="decided_at" type="datetime-local" required aria-label="Decision time" />
          <textarea name="rationale" rows={3} required aria-label="Rationale" placeholder="Rationale" />
          <div>
            <button type="submit">Record decision</button>
          </div>
        </form>
      )}

      <h4>Notification log</h4>
      {current.disclosures.length === 0 && <p>No notifications sent.</p>}
      {current.disclosures.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Sent</th>
              <th>To</th>
              <th>Summary</th>
              <th>Meets</th>
              <th>Attachments</th>
              <th>Recorded by</th>
            </tr>
          </thead>
          <tbody>
            {current.disclosures.map((d) => (
              <tr key={d.id}>
                <td>{when(d.sent_at)}</td>
                <td>
                  {d.recipient} ({d.recipient_type.replace('_', ' ')})
                </td>
                <td>{d.summary}</td>
                <td>{clockName(d.clock_id)}</td>
                <td>{d.evidence_ids.map(evidenceName).join(', ')}</td>
                <td>{actorName(d.recorded_by, members)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {canWrite && (
        <form className="stack" onSubmit={log}>
          <select name="recipient_type" aria-label="Recipient type">
            {RECIPIENTS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <input name="recipient" required maxLength={200} aria-label="Recipient" placeholder="Recipient" />
          <input name="sent_at" type="datetime-local" required aria-label="Sent at" />
          <textarea name="summary" rows={2} required aria-label="Notification summary" placeholder="What was sent, reference numbers" />
          <select name="clock_id" aria-label="Meets clock" defaultValue="">
            <option value="">Meets no clock</option>
            {current.clocks
              .filter((c) => !c.met_at)
              .map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
          </select>
          {attachable.length > 0 && (
            <fieldset>
              <legend>Attachments</legend>
              {attachable.map((e) => (
                <label key={e.id}>
                  <input type="checkbox" name="evidence_ids" value={e.id} /> {e.name}
                </label>
              ))}
            </fieldset>
          )}
          <div>
            <button type="submit">Log notification</button>
          </div>
        </form>
      )}
      {action.error && <p role="alert">{action.error.message}</p>}
    </section>
  )
}
