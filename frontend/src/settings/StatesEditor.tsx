import { useState } from 'react'
import { api, type State } from '../api/client'
import { SaveBar } from './SaveBar'
import { removeAt, updateAt, useSaveSection } from './editing'

export function StatesEditor({ slug, states }: { slug: string; states: State[] }) {
  const [draft, setDraft] = useState(states)
  const save = useSaveSection(slug, (body: State[]) =>
    api.PUT('/api/tenants/{slug}/config/states', { params: { path: { slug } }, body }),
  )

  function move(index: number, offset: number) {
    const next = [...draft]
    ;[next[index], next[index + offset]] = [next[index + offset], next[index]]
    setDraft(next)
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        save.mutate(draft)
      }}
    >
      <h3>Lifecycle states</h3>
      <p>
        Cases start in the first state. Terminal states close a case. Entering a milestone state
        meets that SLA; a gated state needs every task done or waived.
      </p>
      <table>
        <thead>
          <tr>
            <th>Key</th>
            <th>Name</th>
            <th>Terminal</th>
            <th>SLA milestone</th>
            <th>Tasks done gate</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {draft.map((state, i) => (
            <tr key={i}>
              <td>
                <input
                  aria-label={`State ${i + 1} key`}
                  value={state.key}
                  onChange={(e) => setDraft(updateAt(draft, i, { key: e.target.value }))}
                />
              </td>
              <td>
                <input
                  aria-label={`State ${i + 1} name`}
                  value={state.name}
                  onChange={(e) => setDraft(updateAt(draft, i, { name: e.target.value }))}
                />
              </td>
              <td>
                <input
                  type="checkbox"
                  aria-label={`State ${i + 1} terminal`}
                  checked={state.terminal}
                  onChange={(e) => setDraft(updateAt(draft, i, { terminal: e.target.checked }))}
                />
              </td>
              <td>
                <select
                  aria-label={`State ${i + 1} SLA milestone`}
                  value={state.sla_milestone ?? ''}
                  onChange={(e) =>
                    setDraft(
                      updateAt(draft, i, {
                        sla_milestone: (e.target.value || null) as State['sla_milestone'],
                      }),
                    )
                  }
                >
                  <option value="">None</option>
                  <option value="triage">Triage met</option>
                  <option value="contain">Contain met</option>
                </select>
              </td>
              <td>
                <input
                  type="checkbox"
                  aria-label={`State ${i + 1} tasks done gate`}
                  checked={state.require_tasks_done}
                  onChange={(e) =>
                    setDraft(updateAt(draft, i, { require_tasks_done: e.target.checked }))
                  }
                />
              </td>
              <td>
                <button type="button" disabled={i === 0} onClick={() => move(i, -1)}>
                  Up
                </button>
                <button type="button" disabled={i === draft.length - 1} onClick={() => move(i, 1)}>
                  Down
                </button>
                <button type="button" onClick={() => setDraft(removeAt(draft, i))}>
                  Remove
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <button type="button" onClick={() => setDraft([...draft, { key: '', name: '', terminal: false, require_tasks_done: false }])}>
        Add state
      </button>
      <SaveBar save={save} />
    </form>
  )
}
