import { useMutation, useQueryClient } from '@tanstack/react-query'
import type { FormEvent, ReactNode } from 'react'
import {
  api,
  describeError,
  type AssetKind,
  type Case,
  type Member,
  type TenantConfig,
} from '../api/client'
import { useMembers, useTenantConfig } from '../api/queries'
import { Skeleton } from '../Skeleton'
import { useTenant } from '../tenant'
import { CaseForm } from './CaseForm'
import { Custody } from './EvidencePanel'
import { PRIVILEGE_NOTICE } from './journal'
import { useCase, type SplitItems, type SplitSelection } from './useCase'

const ASSET_KINDS: AssetKind[] = ['host', 'account', 'application', 'data_set']
const REPORTS = [
  ['executive', 'Executive summary'],
  ['technical', 'Technical report'],
  ['pir', 'Post-incident review'],
] as const

/** What every frame of the open case works from. */
export interface CaseView {
  slug: string
  current: Case
  config: TenantConfig
  members: Member[]
  permissions: string[]
  canWrite: boolean
  /** Can write and the case is not in a terminal state. */
  editable: boolean
}

/** Loads the open case for one frame and keeps the privilege marking in view above it. */
export function CaseScope({
  number,
  children,
}: {
  number: number
  children: (view: CaseView) => ReactNode
}) {
  const { slug, permissions } = useTenant()
  const config = useTenantConfig(slug)
  const members = useMembers(slug)
  const found = useCase(slug, number)

  if (found.isError) return <p role="alert">{found.error.message}</p>
  if (!found.data || !config.data || !members.data) return <Skeleton />
  const current = found.data
  const canWrite = permissions.includes('cases:write')
  const terminal = config.data.states.some((s) => s.key === current.state && s.terminal)
  return (
    <>
      {current.privileged && <p className="banner sticky">{PRIVILEGE_NOTICE}</p>}
      {children({
        slug,
        current,
        config: config.data,
        members: members.data,
        permissions,
        canWrite,
        editable: canWrite && !terminal,
      })}
    </>
  )
}

function toggle(list: string[], id: string, on: boolean): string[] {
  return on ? [...list, id] : list.filter((x) => x !== id)
}

function SplitCheckbox({
  kind,
  id,
  label,
  split,
}: {
  kind: keyof SplitItems
  id: string
  label: string
  split: SplitSelection
}) {
  return (
    <input
      type="checkbox"
      aria-label={label}
      checked={split[kind].includes(id)}
      onChange={(e) => split.choose(kind, toggle(split[kind], id, e.target.checked))}
    />
  )
}

export function DetailsFrame({ view }: { view: CaseView }) {
  const { slug, current, config, members, canWrite, permissions } = view
  return (
    <CaseForm
      slug={slug}
      current={current}
      config={config}
      members={members}
      canWrite={canWrite}
      canRestrict={permissions.includes('restricted_case')}
    />
  )
}

export function AlertsFrame({ view, split }: { view: CaseView; split: SplitSelection }) {
  const { current, canWrite } = view
  return (
    <section>
      {current.alerts.length === 0 && <p className="empty">No alerts.</p>}
      {current.alerts.map((alert) => (
        <div key={alert.id} className="alert-row">
          {canWrite && (
            <SplitCheckbox kind="alerts" id={alert.id} label={`Select alert ${alert.title}`} split={split} />
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
  )
}

export function AssetsFrame({ view, split }: { view: CaseView; split: SplitSelection }) {
  const { slug, current, canWrite } = view
  const queryClient = useQueryClient()
  const number = current.number
  const change = useMutation({
    mutationFn: async (call: () => Promise<{ error?: unknown; response: Response }>) => {
      const { error, response } = await call()
      if (!response.ok) throw new Error(describeError(error, response.status))
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['case', slug, number] }),
  })

  function addAsset(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const externalId = String(form.get('external_id')).trim()
    event.currentTarget.reset()
    change.mutate(() =>
      api.POST('/api/tenants/{slug}/cases/{number}/assets', {
        params: { path: { slug, number } },
        body: {
          kind: form.get('kind') as AssetKind,
          name: String(form.get('name')),
          external_id: externalId || null,
        },
      }),
    )
  }

  return (
    <section>
      {current.assets.length === 0 && <p className="empty">No assets.</p>}
      <ul>
        {current.assets.map((asset) => (
          <li key={asset.id}>
            {canWrite && (
              <SplitCheckbox kind="assets" id={asset.id} label={`Select asset ${asset.name}`} split={split} />
            )}{' '}
            {asset.kind}: {asset.name}
            {asset.external_id && ` (CMDB ${asset.external_id})`}{' '}
            {canWrite && (
              <button
                type="button"
                onClick={() =>
                  change.mutate(() =>
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
      {change.error && <p role="alert">{change.error.message}</p>}
    </section>
  )
}

export function ReportsFrame({ view }: { view: CaseView }) {
  const { slug, current } = view
  return (
    <ul>
      {REPORTS.map(([kind, label]) => (
        <li key={kind}>
          {label}:{' '}
          {(['pdf', 'docx'] as const).map((format) => (
            <a
              key={format}
              className="report-link"
              href={`/api/tenants/${slug}/cases/${current.number}/reports/${kind}?format=${format}`}
              download
            >
              {label} ({format.toUpperCase()})
            </a>
          ))}
        </li>
      ))}
    </ul>
  )
}

export function CustodyFrame({ view, evidenceId }: { view: CaseView; evidenceId: string }) {
  const { slug, current, canWrite } = view
  const item = current.evidence.find((e) => e.id === evidenceId)
  if (!item) return <p>This evidence is not on the open case.</p>
  return (
    <>
      <h3>{item.name}</h3>
      <Custody slug={slug} number={current.number} item={item} canWrite={canWrite} />
    </>
  )
}
