import { useState } from 'react'
import { api, type ClockTrigger, type Regime, type Regulated } from '../api/client'
import { SaveBar } from './SaveBar'
import { removeAt, splitList, updateAt, useSaveSection } from './editing'

const TRIGGERS: [ClockTrigger, string][] = [
  ['regulated_data', 'Regulated data flagged'],
  ['materiality', 'Materiality decision'],
]
const UNITS: [Regime['unit'], string][] = [
  ['hours', 'hours'],
  ['days', 'calendar days'],
  ['business_days', 'business days'],
]
const REGULATED: Regulated[] = ['pii', 'phi', 'pci', 'cui']

type Row = Omit<Regime, 'regions'> & { regionsText: string }

function toRow({ regions, ...rest }: Regime): Row {
  return { ...rest, regionsText: regions.join(', ') }
}

function toRegime({ regionsText, ...rest }: Row): Regime {
  const regulated_data = rest.trigger === 'regulated_data' ? rest.regulated_data : []
  return { ...rest, regulated_data, regions: splitList(regionsText) }
}

export function RegimesEditor({ slug, regimes }: { slug: string; regimes: Regime[] }) {
  const [draft, setDraft] = useState(() => regimes.map(toRow))
  const save = useSaveSection(slug, (body: Regime[]) =>
    api.PUT('/api/tenants/{slug}/config/regimes', { params: { path: { slug } }, body }),
  )

  function toggleRegulated(index: number, kind: Regulated, on: boolean) {
    const current = draft[index].regulated_data
    const next = on ? [...current, kind] : current.filter((k) => k !== kind)
    setDraft(updateAt(draft, index, { regulated_data: next }))
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        save.mutate(draft.map(toRegime))
      }}
    >
      <h3>Notification regimes</h3>
      <p>
        A clock starts when a case matches. Leave regulated data unchecked to match any kind, and
        regions empty to match anywhere; a region such as US also matches US-CA. Business days skip
        weekends and US federal holidays.
      </p>
      {draft.map((regime, i) => (
        <fieldset key={i}>
          <legend>Regime {i + 1}</legend>
          <div className="stack">
            <label>
              Key{' '}
              <input
                aria-label={`Regime ${i + 1} key`}
                value={regime.key}
                onChange={(e) => setDraft(updateAt(draft, i, { key: e.target.value }))}
              />
            </label>
            <label>
              Name{' '}
              <input
                aria-label={`Regime ${i + 1} name`}
                value={regime.name}
                onChange={(e) => setDraft(updateAt(draft, i, { name: e.target.value }))}
              />
            </label>
            <label>
              Starts on{' '}
              <select
                aria-label={`Regime ${i + 1} trigger`}
                value={regime.trigger}
                onChange={(e) =>
                  setDraft(updateAt(draft, i, { trigger: e.target.value as ClockTrigger }))
                }
              >
                {TRIGGERS.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            {regime.trigger === 'regulated_data' && (
              <div>
                Regulated data{' '}
                {REGULATED.map((kind) => (
                  <label key={kind}>
                    <input
                      type="checkbox"
                      aria-label={`Regime ${i + 1} ${kind}`}
                      checked={regime.regulated_data.includes(kind)}
                      onChange={(e) => toggleRegulated(i, kind, e.target.checked)}
                    />{' '}
                    {kind}
                  </label>
                ))}
              </div>
            )}
            <label>
              Regions{' '}
              <input
                aria-label={`Regime ${i + 1} regions`}
                placeholder="comma separated, e.g. EU, US"
                value={regime.regionsText}
                onChange={(e) => setDraft(updateAt(draft, i, { regionsText: e.target.value }))}
              />
            </label>
            <label>
              Deadline{' '}
              <input
                type="number"
                min={1}
                aria-label={`Regime ${i + 1} duration`}
                value={regime.duration}
                onChange={(e) => setDraft(updateAt(draft, i, { duration: Number(e.target.value) }))}
              />{' '}
              <select
                aria-label={`Regime ${i + 1} unit`}
                value={regime.unit}
                onChange={(e) =>
                  setDraft(updateAt(draft, i, { unit: e.target.value as Regime['unit'] }))
                }
              >
                {UNITS.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Warn hours before the deadline{' '}
              <input
                type="number"
                min={1}
                aria-label={`Regime ${i + 1} warning`}
                value={regime.warn_hours_before}
                onChange={(e) =>
                  setDraft(updateAt(draft, i, { warn_hours_before: Number(e.target.value) }))
                }
              />
            </label>
            <div>
              <button type="button" onClick={() => setDraft(removeAt(draft, i))}>
                Remove regime
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
              key: '',
              name: '',
              trigger: 'regulated_data',
              regulated_data: [],
              regionsText: '',
              duration: 72,
              unit: 'hours',
              warn_hours_before: 24,
            },
          ])
        }
      >
        Add regime
      </button>
      <SaveBar save={save} />
    </form>
  )
}
