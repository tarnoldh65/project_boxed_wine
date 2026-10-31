import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { api, describeError, type AssetKind, type Case, type Me } from '../api/client'
import { useMembers, useTenantConfig } from '../api/queries'
import { useTenant } from '../tenant'
import { CaseForm } from './CaseForm'
import { EvidencePanel } from './EvidencePanel'
import { ObservablesPanel } from './ObservablesPanel'
import { TasksPanel } from './TasksPanel'

const ASSET_KINDS: AssetKind[] = ['host', 'account', 'application', 'data_set']

function toggle(list: string[], id: string, on: boolean): string[] {
  return on ? [...list, id] : list.filter((x) => x !== id)
}

export function CasePage() {
  const { slug, permissions } = useTenant()
  const number = Number(useParams().number)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const config = useTenantConfig(slug)
  const members = useMembers(slug)
  const [selectedAlerts, setSelectedAlerts] = useState<string[]>([])
  const [selectedAssets, setSelectedAssets] = useState<string[]>([])
  const [selectedObservables, setSelectedObservables] = useState<string[]>([])
  const path = { params: { path: { slug, number } } }

  const caseQuery = useQuery({
    queryKey: ['case', slug, number],
    queryFn: async () => {
      const { data, response } = await api.GET('/api/tenants/{slug}/cases/{number}', path)
      if (!data) {
        throw new Error(response.status === 404 ? 'Case not found' : `Error ${response.status}`)
      }
      return data
    },
  })

  // Every case action returns the case to show next.
  const action = useMutation({
    mutationFn: async (run: () => Promise<{ data?: Case; error?: unknown; response: Response }>) => {
      const { data, error, response } = await run()
      if (!data) throw new Error(describeError(error, response.status))
      return data
    },
    onSuccess: (shown) => {
      setSelectedAlerts([])
      setSelectedAssets([])
      setSelectedObservables([])
      queryClient.setQueryData(['case', slug, shown.number], shown)
      void queryClient.invalidateQueries({ queryKey: ['cases', slug] })
      if (shown.number !== number) navigate(`/t/${slug}/cases/${shown.number}`)
    },
  })

  const assets = useMutation({
    mutationFn: async (change: () => Promise<{ error?: unknown; response: Response }>) => {
      const { error, response } = await change()
      if (!response.ok) throw new Error(describeError(error, response.status))
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['case', slug, number] }),
  })

  if (caseQuery.isError) return <p role="alert">{caseQuery.error.message}</p>
  if (!caseQuery.data || !config.data || !members.data) return <p>Loading...</p>
  const current = caseQuery.data
  const canWrite = permissions.includes('cases:write')
  const state = config.data.states.find((s) => s.key === current.state)

  function addAsset(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const externalId = String(form.get('external_id')).trim()
    event.currentTarget.reset()
    assets.mutate(() =>
      api.POST('/api/tenants/{slug}/cases/{number}/assets', {
        ...path,
        body: {
          kind: form.get('kind') as AssetKind,
          name: String(form.get('name')),
          external_id: externalId || null,
        },
      }),
    )
  }

  function setLegalHold(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const reason = String(new FormData(event.currentTarget).get('reason'))
    event.currentTarget.reset()
    action.mutate(() =>
      api.PUT('/api/tenants/{slug}/cases/{number}/legal-hold', {
        ...path,
        body: { on: !current.legal_hold, reason },
      }),
    )
  }

  function declareMajor(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const reason = String(new FormData(event.currentTarget).get('reason'))
    action.mutate(() =>
      api.POST('/api/tenants/{slug}/cases/{number}/major-incident', { ...path, body: { reason } }),
    )
  }

  function merge(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const into = Number(new FormData(event.currentTarget).get('into'))
    action.mutate(() =>
      api.POST('/api/tenants/{slug}/cases/{number}/merge', { ...path, body: { into } }),
    )
  }

  function split(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const title = String(new FormData(event.currentTarget).get('title'))
    action.mutate(() =>
      api.POST('/api/tenants/{slug}/cases/{number}/split', {
        ...path,
        body: {
          title,
          alert_ids: selectedAlerts,
          asset_ids: selectedAssets,
          observable_ids: selectedObservables,
        },
      }),
    )
  }

  return (
    <article>
      <h2>
        #{current.number} {current.title}
      </h2>
      <p>
        {current.priority} {current.severity} | {state?.name ?? current.state} | source{' '}
        {current.source} | opened {new Date(current.created_at).toLocaleString()}
        {current.closed_at && ` | closed ${new Date(current.closed_at).toLocaleString()}`}
      </p>
      {current.merged_into && (
        <p>
          Merged into <Link to={`/t/${slug}/cases/${current.merged_into}`}>#{current.merged_into}</Link>
        </p>
      )}
      {current.legal_hold && (
        <p className="banner">Legal hold: evidence on this case cannot be deleted.</p>
      )}
      {current.major_incident_at && (
        <p className="banner">
          Major incident declared {new Date(current.major_incident_at).toLocaleString()}:{' '}
          {current.major_incident_reason}
        </p>
      )}

      <table className="sla">
        <caption>SLA</caption>
        <tbody>
          <tr>
            {current.sla.map((clock) => (
              <td key={clock.step} className={`sla-${clock.status}`}>
                <strong>{clock.step}</strong> {clock.status}
                <br />
                {clock.met_at
                  ? `met ${new Date(clock.met_at).toLocaleString()}`
                  : `due ${new Date(clock.due_at).toLocaleString()}`}
              </td>
            ))}
          </tr>
        </tbody>
      </table>

      {canWrite && !state?.terminal && (
        <div className="inline-form">
          {!current.acknowledged_at && (
            <button
              type="button"
              onClick={() =>
                action.mutate(() => api.POST('/api/tenants/{slug}/cases/{number}/acknowledge', path))
              }
            >
              Acknowledge
            </button>
          )}
          {!current.major_incident_at && (
            <form className="inline-form" onSubmit={declareMajor}>
              <input name="reason" required maxLength={1000} aria-label="Major incident reason" placeholder="Reason" />
              <button type="submit">Declare major incident</button>
            </form>
          )}
        </div>
      )}

      <CaseForm
        key={current.number}
        slug={slug}
        current={current}
        config={config.data}
        members={members.data}
        canWrite={canWrite}
        canRestrict={permissions.includes('restricted_case')}
      />

      <TasksPanel
        slug={slug}
        current={current}
        members={members.data}
        editable={canWrite && !state?.terminal}
      />

      <section>
        <h3>Alerts</h3>
        {current.alerts.length === 0 && <p>No alerts.</p>}
        {current.alerts.map((alert) => (
          <div key={alert.id} className="alert-row">
            {canWrite && (
              <input
                type="checkbox"
                aria-label={`Select alert ${alert.title}`}
                checked={selectedAlerts.includes(alert.id)}
                onChange={(e) => setSelectedAlerts(toggle(selectedAlerts, alert.id, e.target.checked))}
              />
            )}
            <details>
              <summary>
                {alert.title} ({alert.source}
                {alert.external_id && ` ${alert.external_id}`},{' '}
                {new Date(alert.received_at).toLocaleString()})
              </summary>
              <pre>{JSON.stringify(alert.payload, null, 2)}</pre>
            </details>
          </div>
        ))}
      </section>

      {permissions.includes('legal_hold:manage') && (
        <form className="inline-form" onSubmit={setLegalHold}>
          <input name="reason" required maxLength={1000} aria-label="Legal hold reason" placeholder="Reason" />
          <button type="submit">{current.legal_hold ? 'Lift legal hold' : 'Place legal hold'}</button>
        </form>
      )}

      <EvidencePanel
        slug={slug}
        current={current}
        permissions={permissions}
        collector={queryClient.getQueryData<Me>(['me'])?.display_name ?? ''}
      />

      <ObservablesPanel
        slug={slug}
        current={current}
        editable={canWrite}
        selected={selectedObservables}
        onSelect={setSelectedObservables}
      />

      <section>
        <h3>Assets</h3>
        {current.assets.length === 0 && <p>No assets.</p>}
        <ul>
          {current.assets.map((asset) => (
            <li key={asset.id}>
              {canWrite && (
                <input
                  type="checkbox"
                  aria-label={`Select asset ${asset.name}`}
                  checked={selectedAssets.includes(asset.id)}
                  onChange={(e) =>
                    setSelectedAssets(toggle(selectedAssets, asset.id, e.target.checked))
                  }
                />
              )}{' '}
              {asset.kind}: {asset.name}
              {asset.external_id && ` (CMDB ${asset.external_id})`}{' '}
              {canWrite && (
                <button
                  type="button"
                  onClick={() =>
                    assets.mutate(() =>
                      api.DELETE('/api/tenants/{slug}/cases/{number}/assets/{asset_id}', {
                        params: { path: { slug, number, asset_id: asset.id } },
                      }),
                    )
                  }
                >
                  Remove {asset.name}
                </button>
              )}
            </li>
          ))}
        </ul>
        {canWrite && (
          <form className="inline-form" onSubmit={addAsset}>
            <select name="kind" aria-label="Asset kind">
              {ASSET_KINDS.map((k) => (
                <option key={k} value={k}>
                  {k.replace('_', ' ')}
                </option>
              ))}
            </select>
            <input name="name" aria-label="Asset name" placeholder="Name" required maxLength={200} />
            <input name="external_id" aria-label="CMDB id" placeholder="CMDB id (optional)" />
            <button type="submit">Link asset</button>
          </form>
        )}
        {assets.error && <p role="alert">{assets.error.message}</p>}
      </section>

      {canWrite && !state?.terminal && (
        <section>
          <h3>Merge or split</h3>
          <form className="inline-form" onSubmit={merge}>
            <input name="into" type="number" min={1} required aria-label="Merge into case" placeholder="Case #" />
            <button type="submit">Merge into case</button>
          </form>
          <form className="inline-form" onSubmit={split}>
            <input name="title" required maxLength={300} aria-label="New case title" placeholder="New case title" />
            <button type="submit" disabled={!selectedAlerts.length && !selectedAssets.length && !selectedObservables.length}>
              Split selected into new case
            </button>
          </form>
          {action.error && <p role="alert">{action.error.message}</p>}
        </section>
      )}
    </article>
  )
}
