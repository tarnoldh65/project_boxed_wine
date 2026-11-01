import type { FormEvent } from 'react'
import { api, type Case, type Member, type Milestone } from '../api/client'
import { actorName, useCaseAction } from './journal'

const MILESTONES: [Milestone, string][] = [
  ['first_compromise', 'First compromise'],
  ['detection', 'Detection'],
  ['containment', 'Containment'],
  ['eradication', 'Eradication'],
]

function duration(from: string, to: string): string {
  const hours = (new Date(to).getTime() - new Date(from).getTime()) / 3_600_000
  return hours >= 48 ? `${(hours / 24).toFixed(1)} days` : `${hours.toFixed(1)} hours`
}

export function TimelinePanel({
  slug,
  current,
  members,
  editable,
}: {
  slug: string
  current: Case
  members: Member[]
  editable: boolean
}) {
  const action = useCaseAction(slug, current.number)
  const path = { params: { path: { slug, number: current.number } } }
  const at = Object.fromEntries(
    current.timeline.filter((e) => e.milestone).map((e) => [e.milestone, e.occurred_at]),
  )
  const linked = (id: string | null, list: { id: string; label: string }[]) =>
    list.find((item) => item.id === id)?.label
  const observables = current.observables.map((o) => ({ id: o.id, label: o.defanged }))
  const assets = current.assets.map((a) => ({ id: a.id, label: `${a.kind}: ${a.name}` }))
  const evidence = current.evidence.map((e) => ({ id: e.id, label: e.name }))

  function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    // Link pickers are absent when the case has nothing to link.
    const pick = (name: string) => {
      const value = form.get(name)
      return typeof value === 'string' && value ? value : null
    }
    event.currentTarget.reset()
    action.mutate(() =>
      api.POST('/api/tenants/{slug}/cases/{number}/timeline', {
        ...path,
        body: {
          occurred_at: new Date(String(form.get('occurred_at'))).toISOString(),
          source: String(form.get('source')),
          description: String(form.get('description')),
          milestone: pick('milestone') as Milestone | null,
          observable_id: pick('observable_id'),
          asset_id: pick('asset_id'),
          evidence_id: pick('evidence_id'),
        },
      }),
    )
  }

  function remove(eventId: string) {
    action.mutate(async () => {
      const { error, response } = await api.DELETE(
        '/api/tenants/{slug}/cases/{number}/timeline/{event_id}',
        { params: { path: { slug, number: current.number, event_id: eventId } } },
      )
      if (!response.ok) return { error, response }
      return api.GET('/api/tenants/{slug}/cases/{number}', path)
    })
  }

  const select = (name: string, label: string, items: { id: string; label: string }[]) =>
    items.length > 0 && (
      <select name={name} aria-label={label} defaultValue="">
        <option value="">{label}: none</option>
        {items.map((item) => (
          <option key={item.id} value={item.id}>
            {item.label}
          </option>
        ))}
      </select>
    )

  return (
    <section>
      <h3>Timeline</h3>
      <dl className="milestones">
        {MILESTONES.map(([key, label]) => (
          <div key={key}>
            <dt>{label}</dt>
            <dd>{at[key] ? new Date(at[key]).toLocaleString() : 'not recorded'}</dd>
          </div>
        ))}
        {at.first_compromise && at.detection && (
          <div>
            <dt>Time to detect</dt>
            <dd>{duration(at.first_compromise, at.detection)}</dd>
          </div>
        )}
        {at.first_compromise && at.eradication && (
          <div>
            <dt>Dwell time</dt>
            <dd>{duration(at.first_compromise, at.eradication)}</dd>
          </div>
        )}
      </dl>
      {current.timeline.length === 0 && <p>No events recorded.</p>}
      {current.timeline.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>When</th>
              <th>Source</th>
              <th>What happened</th>
              <th>Related</th>
              <th>Recorded</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {current.timeline.map((e) => (
              <tr key={e.id}>
                <td>{new Date(e.occurred_at).toLocaleString()}</td>
                <td>{e.source}</td>
                <td>
                  {e.milestone && <span className="badge">{e.milestone.replace('_', ' ')}</span>}{' '}
                  {e.description}
                </td>
                <td>
                  {[linked(e.observable_id, observables), linked(e.asset_id, assets), linked(e.evidence_id, evidence)]
                    .filter(Boolean)
                    .join(', ')}
                </td>
                <td>
                  {actorName(e.recorded_by, members)}, {new Date(e.recorded_at).toLocaleString()}
                </td>
                <td>
                  {editable && (
                    <button type="button" onClick={() => remove(e.id)}>
                      Remove event {e.description}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {editable && (
        <form className="inline-form" onSubmit={add}>
          <input name="occurred_at" type="datetime-local" required aria-label="Event time" />
          <input name="source" required maxLength={200} aria-label="Event source" placeholder="Source" />
          <input name="description" required aria-label="Event description" placeholder="What happened" />
          <select name="milestone" aria-label="Milestone" defaultValue="">
            <option value="">No milestone</option>
            {MILESTONES.filter(([key]) => !at[key]).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
          {select('observable_id', 'Observable', observables)}
          {select('asset_id', 'Asset', assets)}
          {select('evidence_id', 'Evidence', evidence)}
          <button type="submit">Add event</button>
        </form>
      )}
      {action.error && <p role="alert">{action.error.message}</p>}
    </section>
  )
}
