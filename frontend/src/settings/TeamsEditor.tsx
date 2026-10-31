import { useState } from 'react'
import { api, type Member, type Team } from '../api/client'
import { SaveBar } from './SaveBar'
import { removeAt, updateAt, useSaveSection } from './editing'

export function TeamsEditor({
  slug,
  teams,
  members,
}: {
  slug: string
  teams: Team[]
  members: Member[]
}) {
  const [draft, setDraft] = useState(teams)
  const save = useSaveSection(slug, (body: Team[]) =>
    api.PUT('/api/tenants/{slug}/config/teams', { params: { path: { slug } }, body }),
  )

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        save.mutate(draft)
      }}
    >
      <h3>Teams</h3>
      <p>New intake cases go to the on-call member of the team their template names.</p>
      <table>
        <thead>
          <tr>
            <th>Key</th>
            <th>Name</th>
            <th>On call</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {draft.map((team, i) => (
            <tr key={i}>
              <td>
                <input
                  aria-label={`Team ${i + 1} key`}
                  value={team.key}
                  onChange={(e) => setDraft(updateAt(draft, i, { key: e.target.value }))}
                />
              </td>
              <td>
                <input
                  aria-label={`Team ${i + 1} name`}
                  value={team.name}
                  onChange={(e) => setDraft(updateAt(draft, i, { name: e.target.value }))}
                />
              </td>
              <td>
                <select
                  aria-label={`Team ${i + 1} on call`}
                  value={team.on_call ?? ''}
                  onChange={(e) => setDraft(updateAt(draft, i, { on_call: e.target.value || null }))}
                >
                  <option value="">Nobody</option>
                  {members.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.display_name}
                    </option>
                  ))}
                </select>
              </td>
              <td>
                <button type="button" onClick={() => setDraft(removeAt(draft, i))}>
                  Remove
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <button type="button" onClick={() => setDraft([...draft, { key: '', name: '', on_call: null }])}>
        Add team
      </button>
      <SaveBar save={save} />
    </form>
  )
}
