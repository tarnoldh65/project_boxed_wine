import { useMutation, useQuery } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import { api, describeError } from '../api/client'
import { useMembers, useTenantConfig } from '../api/queries'
import { useTenant } from '../tenant'

export function CasesPage() {
  const { slug, permissions } = useTenant()
  const navigate = useNavigate()
  const [state, setState] = useState('')
  const config = useTenantConfig(slug)
  const members = useMembers(slug)
  const cases = useQuery({
    queryKey: ['cases', slug, state],
    queryFn: async () => {
      const { data, response } = await api.GET('/api/tenants/{slug}/cases', {
        params: { path: { slug }, query: state ? { state } : {} },
      })
      if (!data) throw new Error(`Could not load cases (${response.status})`)
      return data
    },
  })

  const create = useMutation({
    mutationFn: async (body: { template: string; title: string; description: string }) => {
      const { data, error, response } = await api.POST('/api/tenants/{slug}/cases', {
        params: { path: { slug } },
        body,
      })
      if (!data) throw new Error(describeError(error, response.status))
      return data
    },
    onSuccess: (created) => navigate(`/t/${slug}/cases/${created.number}`),
  })

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    create.mutate({
      template: String(form.get('template')),
      title: String(form.get('title')),
      description: String(form.get('description')),
    })
  }

  const stateName = (key: string) => config.data?.states.find((s) => s.key === key)?.name ?? key
  const ownerName = (id: string | null) =>
    members.data?.find((m) => m.id === id)?.display_name ?? (id ? 'Unknown' : 'Unassigned')

  return (
    <>
      {permissions.includes('cases:write') && config.data && (
        <details className="panel">
          <summary>New case</summary>
          <form className="stack" onSubmit={onSubmit}>
            <label>
              Type{' '}
              <select name="template">
                {config.data.templates.map((t) => (
                  <option key={t.key} value={t.key}>
                    {t.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Title <input name="title" required maxLength={300} />
            </label>
            <label>
              Description <textarea name="description" rows={3} />
            </label>
            <div>
              <button type="submit" disabled={create.isPending}>
                Create case
              </button>
            </div>
            {create.isError && <p role="alert">{create.error.message}</p>}
          </form>
        </details>
      )}

      <section>
        <h2>Cases</h2>
        <label>
          State{' '}
          <select value={state} onChange={(e) => setState(e.target.value)}>
            <option value="">All</option>
            {config.data?.states.map((s) => (
              <option key={s.key} value={s.key}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
        {cases.isError && <p role="alert">{cases.error.message}</p>}
        {cases.data?.length === 0 && <p>No cases yet.</p>}
        {!!cases.data?.length && (
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>Title</th>
                <th>State</th>
                <th>Severity</th>
                <th>Owner</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {cases.data.map((c) => (
                <tr key={c.number}>
                  <td>{c.number}</td>
                  <td>
                    <Link to={`/t/${slug}/cases/${c.number}`}>{c.title}</Link>
                    {c.restricted && ' (restricted)'}
                  </td>
                  <td>{stateName(c.state)}</td>
                  <td>
                    {c.priority} {c.severity}
                  </td>
                  <td>{ownerName(c.owner_id)}</td>
                  <td>{new Date(c.created_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  )
}
