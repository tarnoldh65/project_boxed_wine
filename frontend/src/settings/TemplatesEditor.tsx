import { useState } from 'react'
import { api, type Pap, type Template, type TenantConfig, type Tlp } from '../api/client'
import { SaveBar } from './SaveBar'
import { removeAt, updateAt, useSaveSection } from './editing'

const TLPS: Tlp[] = ['clear', 'green', 'amber', 'amber+strict', 'red']
const PAPS: Pap[] = ['clear', 'green', 'amber', 'red']

export function TemplatesEditor({ slug, config }: { slug: string; config: TenantConfig }) {
  const [draft, setDraft] = useState(config.templates)
  const save = useSaveSection(slug, (body: Template[]) =>
    api.PUT('/api/tenants/{slug}/config/templates', { params: { path: { slug } }, body }),
  )
  const { impacts, urgencies } = config.severity

  function select<K extends keyof Template>(i: number, key: K, label: string, values: string[]) {
    return (
      <select
        aria-label={`Template ${i + 1} ${label}`}
        value={draft[i][key] ?? ''}
        onChange={(e) => setDraft(updateAt(draft, i, { [key]: e.target.value || null }))}
      >
        {key === 'team' && <option value="">None</option>}
        {values.map((v) => (
          <option key={v}>{v}</option>
        ))}
      </select>
    )
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        save.mutate(draft)
      }}
    >
      <h3>Case templates</h3>
      <p>Each incident type sets a new case's category and starting defaults.</p>
      <table>
        <thead>
          <tr>
            <th>Key</th>
            <th>Name</th>
            <th>Impact</th>
            <th>Urgency</th>
            <th>TLP</th>
            <th>PAP</th>
            <th>Team</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {draft.map((template, i) => (
            <tr key={i}>
              <td>
                <input
                  aria-label={`Template ${i + 1} key`}
                  value={template.key}
                  onChange={(e) => setDraft(updateAt(draft, i, { key: e.target.value }))}
                />
              </td>
              <td>
                <input
                  aria-label={`Template ${i + 1} name`}
                  value={template.name}
                  onChange={(e) => setDraft(updateAt(draft, i, { name: e.target.value }))}
                />
              </td>
              <td>{select(i, 'impact', 'impact', impacts)}</td>
              <td>{select(i, 'urgency', 'urgency', urgencies)}</td>
              <td>{select(i, 'tlp', 'TLP', TLPS)}</td>
              <td>{select(i, 'pap', 'PAP', PAPS)}</td>
              <td>{select(i, 'team', 'team', config.teams.map((t) => t.key))}</td>
              <td>
                <button type="button" onClick={() => setDraft(removeAt(draft, i))}>
                  Remove
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <button
        type="button"
        onClick={() =>
          setDraft([
            ...draft,
            {
              key: '',
              name: '',
              impact: impacts[0],
              urgency: urgencies[0],
              tlp: 'amber',
              pap: 'amber',
              team: null,
            },
          ])
        }
      >
        Add template
      </button>
      <SaveBar save={save} />
    </form>
  )
}
