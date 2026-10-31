import { useState } from 'react'
import { api, type Severity, type SeverityLevel } from '../api/client'
import { SaveBar } from './SaveBar'
import { removeAt, splitList, updateAt, useSaveSection } from './editing'

const SLA_STEPS = [
  ['acknowledge_minutes', 'Acknowledge'],
  ['triage_minutes', 'Triage'],
  ['contain_minutes', 'Contain'],
  ['resolve_minutes', 'Resolve'],
] as const

const cell = (impact: string, urgency: string) => `${impact}|${urgency}`

interface Draft {
  levels: SeverityLevel[]
  impacts: string
  urgencies: string
  cells: Record<string, string>
}

function toDraft(severity: Severity): Draft {
  const cells: Record<string, string> = {}
  severity.impacts.forEach((impact, i) =>
    severity.urgencies.forEach((urgency, u) => {
      cells[cell(impact, urgency)] = severity.matrix[i][u]
    }),
  )
  return {
    levels: severity.levels,
    impacts: severity.impacts.join(', '),
    urgencies: severity.urgencies.join(', '),
    cells,
  }
}

export function SeverityEditor({ slug, severity }: { slug: string; severity: Severity }) {
  const [draft, setDraft] = useState(() => toDraft(severity))
  const save = useSaveSection(slug, (body: Severity) =>
    api.PUT('/api/tenants/{slug}/config/severity', { params: { path: { slug } }, body }),
  )
  const impacts = splitList(draft.impacts)
  const urgencies = splitList(draft.urgencies)
  // New matrix cells default to the least severe level.
  const fallback = draft.levels.at(-1)?.key ?? ''
  const valueAt = (impact: string, urgency: string) =>
    draft.cells[cell(impact, urgency)] ?? fallback
  const setLevels = (levels: SeverityLevel[]) => setDraft({ ...draft, levels })

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        save.mutate({
          levels: draft.levels,
          impacts,
          urgencies,
          matrix: impacts.map((impact) => urgencies.map((urgency) => valueAt(impact, urgency))),
        })
      }}
    >
      <h3>Severity and SLAs</h3>
      <p>Levels are listed most severe first; the first is priority P1. SLA targets are minutes.</p>
      <table>
        <thead>
          <tr>
            <th>Level</th>
            {SLA_STEPS.map(([, label]) => (
              <th key={label}>{label}</th>
            ))}
            <th />
          </tr>
        </thead>
        <tbody>
          {draft.levels.map((level, i) => (
            <tr key={i}>
              <td>
                <input
                  aria-label={`Level ${i + 1} key`}
                  value={level.key}
                  onChange={(e) => setLevels(updateAt(draft.levels, i, { key: e.target.value }))}
                />
              </td>
              {SLA_STEPS.map(([step, label]) => (
                <td key={step}>
                  <input
                    type="number"
                    min={1}
                    aria-label={`Level ${i + 1} ${label.toLowerCase()} minutes`}
                    value={level.sla[step]}
                    onChange={(e) =>
                      setLevels(
                        updateAt(draft.levels, i, {
                          sla: { ...level.sla, [step]: Number(e.target.value) },
                        }),
                      )
                    }
                  />
                </td>
              ))}
              <td>
                <button type="button" onClick={() => setLevels(removeAt(draft.levels, i))}>
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
          setLevels([
            ...draft.levels,
            {
              key: '',
              sla: {
                acknowledge_minutes: 60,
                triage_minutes: 240,
                contain_minutes: 1440,
                resolve_minutes: 4320,
              },
            },
          ])
        }
      >
        Add level
      </button>

      <div className="stack">
        <label>
          Impacts{' '}
          <input
            value={draft.impacts}
            onChange={(e) => setDraft({ ...draft, impacts: e.target.value })}
          />
        </label>
        <label>
          Urgencies{' '}
          <input
            value={draft.urgencies}
            onChange={(e) => setDraft({ ...draft, urgencies: e.target.value })}
          />
        </label>
      </div>

      <table>
        <caption>Severity by impact (rows) and urgency (columns)</caption>
        <thead>
          <tr>
            <th />
            {urgencies.map((urgency) => (
              <th key={urgency}>{urgency}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {impacts.map((impact) => (
            <tr key={impact}>
              <th>{impact}</th>
              {urgencies.map((urgency) => (
                <td key={urgency}>
                  <select
                    aria-label={`Severity for ${impact} impact, ${urgency} urgency`}
                    value={valueAt(impact, urgency)}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        cells: { ...draft.cells, [cell(impact, urgency)]: e.target.value },
                      })
                    }
                  >
                    {draft.levels.map((level) => (
                      <option key={level.key}>{level.key}</option>
                    ))}
                  </select>
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <SaveBar save={save} />
    </form>
  )
}
