import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { api, describeError, type Candidate, type Case, type ObservableType } from '../api/client'

const TYPES: ObservableType[] = [
  'ipv4', 'ipv6', 'domain', 'url', 'md5', 'sha1', 'sha256', 'email', 'filename',
  'registry_key', 'user', 'process', 'cve',
]

const key = (c: { type: string; value: string }) => `${c.type}|${c.value}`

export function ObservablesPanel({
  slug,
  current,
  editable,
  selected,
  onSelect,
}: {
  slug: string
  current: Case
  editable: boolean
  selected: string[]
  onSelect: (ids: string[]) => void
}) {
  const queryClient = useQueryClient()
  const path = { params: { path: { slug, number: current.number } } }
  const [candidates, setCandidates] = useState<Candidate[] | null>(null)
  const [chosen, setChosen] = useState<string[]>([])

  const extract = useMutation({
    mutationFn: async (text: string | null) => {
      const { data, error, response } = await api.POST(
        '/api/tenants/{slug}/cases/{number}/observables/extract',
        { ...path, body: { text } },
      )
      if (!data) throw new Error(describeError(error, response.status))
      return data
    },
    onSuccess: (found) => {
      setCandidates(found)
      setChosen(found.filter((c) => !c.linked).map(key))
    },
  })

  const change = useMutation({
    mutationFn: async (call: () => Promise<{ data?: Case; error?: unknown; response: Response }>) => {
      const { data, error, response } = await call()
      if (!response.ok) throw new Error(describeError(error, response.status))
      return data
    },
    onSuccess: (updated) => {
      if (updated) queryClient.setQueryData(['case', slug, current.number], updated)
      else void queryClient.invalidateQueries({ queryKey: ['case', slug, current.number] })
    },
  })

  function linkObservables(items: { type: ObservableType; value: string }[]) {
    change.mutate(() =>
      api.POST('/api/tenants/{slug}/cases/{number}/observables', { ...path, body: { observables: items } }),
    )
  }

  function linkChosen() {
    linkObservables(
      (candidates ?? [])
        .filter((c) => chosen.includes(key(c)))
        .map(({ type, value }) => ({ type, value })),
    )
    setCandidates(null)
  }

  function addManual(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    event.currentTarget.reset()
    linkObservables([{ type: form.get('type') as ObservableType, value: String(form.get('value')) }])
  }

  function extractText(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    extract.mutate(String(new FormData(event.currentTarget).get('text')))
  }

  return (
    <section>
      <h3>Observables</h3>
      {current.observables.length === 0 && <p>No observables.</p>}
      {current.observables.length > 0 && (
        <table>
          <tbody>
            {current.observables.map((o) => (
              <tr key={o.id}>
                {editable && (
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`Select observable ${o.defanged}`}
                      checked={selected.includes(o.id)}
                      onChange={(e) =>
                        onSelect(e.target.checked ? [...selected, o.id] : selected.filter((x) => x !== o.id))
                      }
                    />
                  </td>
                )}
                <td>{o.type}</td>
                <td>
                  <Link to={`/t/${slug}/observables/${o.id}`}>
                    <code>{o.defanged}</code>
                  </Link>
                </td>
                <td>{o.other_cases > 0 ? `seen in ${o.other_cases} other case${o.other_cases > 1 ? 's' : ''}` : ''}</td>
                {editable && (
                  <td>
                    <button
                      type="button"
                      onClick={() =>
                        change.mutate(() =>
                          api.DELETE('/api/tenants/{slug}/cases/{number}/observables/{observable_id}', {
                            params: { path: { slug, number: current.number, observable_id: o.id } },
                          }),
                        )
                      }
                    >
                      Remove {o.defanged}
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {editable && (
        <details className="panel">
          <summary>Add observables</summary>
          <form className="stack" onSubmit={extractText}>
            <label>
              Paste text, an email, or log lines{' '}
              <textarea name="text" rows={5} required />
            </label>
            <div className="inline-form">
              <button type="submit">Extract from text</button>
              {current.alerts.length > 0 && (
                <button type="button" onClick={() => extract.mutate(null)}>
                  Extract from alerts
                </button>
              )}
            </div>
          </form>
          {extract.error && <p role="alert">{extract.error.message}</p>}
          {candidates?.length === 0 && <p>Nothing found.</p>}
          {!!candidates?.length && (
            <div>
              <p>Review before linking:</p>
              <ul className="checklist">
                {candidates.map((c) => (
                  <li key={key(c)}>
                    <label>
                      <input
                        type="checkbox"
                        disabled={c.linked}
                        checked={c.linked || chosen.includes(key(c))}
                        onChange={(e) =>
                          setChosen(e.target.checked ? [...chosen, key(c)] : chosen.filter((x) => x !== key(c)))
                        }
                      />{' '}
                      {c.type} <code>{c.defanged}</code>
                      {c.linked && ' (already linked)'}
                    </label>
                  </li>
                ))}
              </ul>
              <button type="button" disabled={chosen.length === 0} onClick={linkChosen}>
                Link selected
              </button>
            </div>
          )}
          <form className="inline-form" onSubmit={addManual}>
            <select name="type" aria-label="Observable type">
              {TYPES.map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
            <input name="value" required aria-label="Observable value" placeholder="Value (defanged is fine)" />
            <button type="submit">Add</button>
          </form>
        </details>
      )}
      {change.error && <p role="alert">{change.error.message}</p>}
    </section>
  )
}
