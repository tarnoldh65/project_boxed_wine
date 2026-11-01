import type { FormEvent } from 'react'
import { api, type Case, type Member } from '../api/client'
import { useCaseAction } from './journal'

const SECTIONS = [
  ['root_cause', 'Root cause'],
  ['contributing_factors', 'Contributing factors'],
  ['went_well', 'What went well'],
  ['went_poorly', 'What did not go well'],
] as const

export function PirPanel({
  slug,
  current,
  members,
  canWrite,
}: {
  slug: string
  current: Case
  members: Member[]
  canWrite: boolean
}) {
  const action = useCaseAction(slug, current.number)
  const path = { params: { path: { slug, number: current.number } } }
  const review = current.pir
  const ownerName = (id: string | null) =>
    id ? (members.find((m) => m.id === id)?.display_name ?? 'Former member') : 'Unassigned'

  function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const text = (name: string) => String(form.get(name))
    action.mutate(() =>
      api.PUT('/api/tenants/{slug}/cases/{number}/pir', {
        ...path,
        body: {
          root_cause: text('root_cause'),
          contributing_factors: text('contributing_factors'),
          went_well: text('went_well'),
          went_poorly: text('went_poorly'),
        },
      }),
    )
  }

  function addAction(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const due = String(form.get('due_at'))
    event.currentTarget.reset()
    action.mutate(() =>
      api.POST('/api/tenants/{slug}/cases/{number}/pir/actions', {
        ...path,
        body: {
          title: String(form.get('title')),
          owner_id: String(form.get('owner_id')) || null,
          due_at: due ? new Date(due).toISOString() : null,
        },
      }),
    )
  }

  function setDone(actionId: string, done: boolean) {
    action.mutate(() =>
      api.PATCH('/api/tenants/{slug}/cases/{number}/pir/actions/{action_id}', {
        params: { path: { slug, number: current.number, action_id: actionId } },
        body: { done },
      }),
    )
  }

  return (
    <section>
      <h3>Post-incident review</h3>
      <p>
        {review.complete
          ? 'Complete.'
          : 'Incomplete: every section must be filled in before a true positive can close.'}
      </p>
      <form key={review.updated_at ?? ''} className="stack" onSubmit={save}>
        {SECTIONS.map(([name, label]) => (
          <label key={name}>
            {label}
            <textarea name={name} rows={3} defaultValue={review[name]} readOnly={!canWrite} />
          </label>
        ))}
        {canWrite && (
          <div>
            <button type="submit">Save review</button>
          </div>
        )}
      </form>

      <h4>Action items</h4>
      {review.actions.length === 0 && <p>No action items.</p>}
      {review.actions.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Action</th>
              <th>Owner</th>
              <th>Due</th>
              <th>Status</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {review.actions.map((item) => (
              <tr key={item.id}>
                <td>{item.title}</td>
                <td>{ownerName(item.owner_id)}</td>
                <td>{item.due_at && new Date(item.due_at).toLocaleString()}</td>
                <td>{item.done_at ? `done ${new Date(item.done_at).toLocaleString()}` : 'open'}</td>
                <td>
                  {canWrite && (
                    <button type="button" onClick={() => setDone(item.id, !item.done_at)}>
                      {item.done_at ? 'Reopen' : 'Mark done'} {item.title}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {canWrite && (
        <form className="inline-form" onSubmit={addAction}>
          <input name="title" required maxLength={300} aria-label="Action item" placeholder="Action item" />
          <select name="owner_id" aria-label="Action owner" defaultValue="">
            <option value="">Unassigned</option>
            {members.map((m) => (
              <option key={m.id} value={m.id}>
                {m.display_name}
              </option>
            ))}
          </select>
          <input name="due_at" type="datetime-local" aria-label="Action due" />
          <button type="submit">Add action item</button>
        </form>
      )}
      {action.error && <p role="alert">{action.error.message}</p>}
    </section>
  )
}
