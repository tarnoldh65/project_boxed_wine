import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import {
  api,
  describeError,
  type Case,
  type Collection,
  type CustodyReport,
  type Evidence,
} from '../api/client'

type Mode = 'file' | 'malware' | 'reference'
const DOWNLOADABLE = ['stored', 'hashed', 'mismatch']

function formatSize(bytes: number | null): string {
  if (bytes === null) return ''
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${unit === 0 ? value : value.toFixed(1)} ${units[unit]}`
}

function nowLocal(): string {
  const now = new Date()
  return new Date(now.getTime() - now.getTimezoneOffset() * 60_000).toISOString().slice(0, 16)
}

function collectionFrom(form: FormData): Collection {
  return {
    collected_by: String(form.get('collected_by')),
    collected_at: new Date(String(form.get('collected_at'))).toISOString(),
    collected_from: String(form.get('collected_from')) || null,
    method: String(form.get('method')) || null,
  }
}

async function ok(response: Response, error: unknown = undefined): Promise<void> {
  if (!response.ok) throw new Error(describeError(error ?? (await response.json().catch(() => null)), response.status))
}

function Custody({
  slug,
  number,
  item,
  canWrite,
}: {
  slug: string
  number: number
  item: Evidence
  canWrite: boolean
}) {
  const queryClient = useQueryClient()
  const key = ['custody', item.id]
  const path = { params: { path: { slug, number, evidence_id: item.id } } }
  const report = useQuery({
    queryKey: key,
    queryFn: async (): Promise<CustodyReport> => {
      const { data, error, response } = await api.GET(
        '/api/tenants/{slug}/cases/{number}/evidence/{evidence_id}/custody',
        path,
      )
      if (!data) throw new Error(describeError(error, response.status))
      return data
    },
  })
  const transfer = useMutation({
    mutationFn: async (body: { to: string; reason: string; location: string | null }) => {
      const { data, error, response } = await api.POST(
        '/api/tenants/{slug}/cases/{number}/evidence/{evidence_id}/transfer',
        { ...path, body },
      )
      if (!data) throw new Error(describeError(error, response.status))
      return data
    },
    onSuccess: (updated) => queryClient.setQueryData(key, updated),
  })

  function save() {
    const blob = new Blob([JSON.stringify(report.data, null, 2)], { type: 'application/json' })
    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    link.download = `custody-${item.name}.json`
    link.click()
    URL.revokeObjectURL(link.href)
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    event.currentTarget.reset()
    transfer.mutate({
      to: String(form.get('to')),
      reason: String(form.get('reason')),
      location: String(form.get('location')) || null,
    })
  }

  if (report.isError) return <p role="alert">{report.error.message}</p>
  if (!report.data) return <p>Loading custody...</p>
  return (
    <div className="custody">
      <p>
        Chain of custody {report.data.chain_intact ? 'verified intact' : 'BROKEN: entries were altered'}{' '}
        <button type="button" onClick={save}>
          Save report
        </button>
      </p>
      <table>
        <thead>
          <tr>
            <th>When</th>
            <th>Action</th>
            <th>By</th>
            <th>Details</th>
          </tr>
        </thead>
        <tbody>
          {report.data.entries.map((entry) => (
            <tr key={entry.hash}>
              <td>{new Date(entry.at).toLocaleString()}</td>
              <td>{entry.action}</td>
              <td>{entry.actor}</td>
              <td>
                {Object.entries(entry.detail)
                  .filter(([, v]) => v !== null)
                  .map(([k, v]) => `${k}: ${v}`)
                  .join('; ')}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {canWrite && item.status !== 'deleted' && (
        <form className="inline-form" onSubmit={submit}>
          <input name="to" required aria-label="Transfer to" placeholder="Transfer to" />
          <input name="reason" required aria-label="Transfer reason" placeholder="Reason" />
          <input name="location" aria-label="Transfer location" placeholder="Location (optional)" />
          <button type="submit">Record transfer</button>
        </form>
      )}
      {transfer.error && <p role="alert">{transfer.error.message}</p>}
    </div>
  )
}

export function EvidencePanel({
  slug,
  current,
  permissions,
  collector,
}: {
  slug: string
  current: Case
  permissions: string[]
  collector: string
}) {
  const queryClient = useQueryClient()
  const base = `/api/tenants/${slug}/cases/${current.number}/evidence`
  const canWrite = permissions.includes('cases:write')
  const [mode, setMode] = useState<Mode>('file')
  const [progress, setProgress] = useState<string | null>(null)
  const [openCustody, setOpenCustody] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['case', slug, current.number] })

  const add = useMutation({
    mutationFn: async ({ form, file }: { form: FormData; file: File | null }) => {
      const collection = collectionFrom(form)
      const description = String(form.get('description'))
      if (mode === 'reference') {
        const { error, response } = await api.POST('/api/tenants/{slug}/cases/{number}/evidence/references', {
          params: { path: { slug, number: current.number } },
          body: {
            name: String(form.get('name')),
            description,
            location: String(form.get('location')),
            sha256: String(form.get('sha256')) || null,
            size: form.get('size') ? Number(form.get('size')) : null,
            collection,
          },
        })
        return ok(response, error)
      }
      if (!file) throw new Error('Choose a file to add')
      if (mode === 'malware') {
        const body = new FormData()
        body.set('file', file)
        body.set('description', description)
        for (const [k, v] of Object.entries(collection)) if (v) body.set(k, v)
        const response = await fetch(`${window.location.origin}${base}/malware`, {
          method: 'POST',
          body,
        })
        return ok(response)
      }
      // Large files go from the browser straight to object storage, one part at a time.
      const { data: started, error, response } = await api.POST(
        '/api/tenants/{slug}/cases/{number}/evidence/uploads',
        {
          params: { path: { slug, number: current.number } },
          body: { name: file.name, size: file.size, description, collection },
        },
      )
      if (!started) return ok(response, error)
      const id = started.evidence.id
      try {
        for (const [i, url] of started.part_urls.entries()) {
          setProgress(`Uploading part ${i + 1} of ${started.part_urls.length}`)
          const part = file.slice(i * started.part_size, (i + 1) * started.part_size)
          await ok(await fetch(new Request(url, { method: 'PUT', body: part })))
        }
        await ok(await fetch(new Request(`${window.location.origin}${base}/${id}/complete`, { method: 'POST' })))
      } catch (error) {
        await fetch(new Request(`${window.location.origin}${base}/${id}/abort`, { method: 'POST' }))
        throw error
      }
    },
    onSettled: () => {
      setProgress(null)
      return refresh()
    },
  })

  const remove = useMutation({
    mutationFn: async ({ id, reason }: { id: string; reason: string }) => {
      const { error, response } = await api.DELETE(
        '/api/tenants/{slug}/cases/{number}/evidence/{evidence_id}',
        { params: { path: { slug, number: current.number, evidence_id: id }, query: { reason } } },
      )
      await ok(response, error)
    },
    onSuccess: () => {
      setDeleting(null)
      return refresh()
    },
  })

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const input = event.currentTarget.elements.namedItem('file') as HTMLInputElement | null
    add.mutate({ form: new FormData(event.currentTarget), file: input?.files?.[0] ?? null })
  }

  function submitDelete(id: string, event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    remove.mutate({ id, reason: String(new FormData(event.currentTarget).get('reason')) })
  }

  return (
    <section>
      <h3>Evidence</h3>
      {current.evidence.length === 0 && <p>No evidence.</p>}
      {current.evidence.map((item) => (
        <div key={item.id} className="task">
          <div className="task-head">
            <strong>{item.name}</strong>
            <span className="badge">{item.kind}</span>
            <span>{item.status}</span>
            <span>{formatSize(item.size)}</span>
            {item.sha256 && <code title={item.sha256}>sha256 {item.sha256.slice(0, 12)}</code>}
            {DOWNLOADABLE.includes(item.status) &&
              (item.kind !== 'malware' || permissions.includes('malware:read')) && (
                <a href={`${base}/${item.id}/download`} download>
                  Download {item.name}
                </a>
              )}
            <button
              type="button"
              onClick={() => setOpenCustody(openCustody === item.id ? null : item.id)}
            >
              Custody of {item.name}
            </button>
            {canWrite && item.status !== 'deleted' && (
              <button
                type="button"
                disabled={current.legal_hold}
                title={current.legal_hold ? 'The case is under legal hold' : undefined}
                onClick={() => setDeleting(item.id)}
              >
                Delete {item.name}
              </button>
            )}
          </div>
          {item.kind === 'reference' && <p>Held at: {item.location}</p>}
          {item.status === 'mismatch' && (
            <p role="alert">Integrity check failed: the stored content no longer matches its hash.</p>
          )}
          {deleting === item.id && (
            <form className="inline-form" onSubmit={(e) => submitDelete(item.id, e)}>
              <input name="reason" required aria-label={`Reason to delete ${item.name}`} placeholder="Reason" />
              <button type="submit">Confirm delete</button>
              <button type="button" onClick={() => setDeleting(null)}>
                Cancel
              </button>
            </form>
          )}
          {openCustody === item.id && (
            <Custody slug={slug} number={current.number} item={item} canWrite={canWrite} />
          )}
        </div>
      ))}
      {remove.error && <p role="alert">{remove.error.message}</p>}

      {canWrite && (
        <details className="panel">
          <summary>Add evidence</summary>
          <form className="stack" onSubmit={submit}>
            <fieldset>
              <legend>Kind</legend>
              {(
                [
                  ['file', 'Upload a file'],
                  ['malware', 'Malware sample (stored encrypted)'],
                  ['reference', 'Held elsewhere'],
                ] as const
              ).map(([value, label]) => (
                <label key={value}>
                  <input
                    type="radio"
                    name="mode"
                    value={value}
                    checked={mode === value}
                    onChange={() => setMode(value)}
                  />{' '}
                  {label}
                </label>
              ))}
            </fieldset>
            {mode === 'reference' ? (
              <>
                <label>
                  Name <input name="name" required maxLength={255} />
                </label>
                <label>
                  Location <input name="location" required placeholder="Locker, bag, or storage URI" />
                </label>
                <label>
                  SHA-256 <input name="sha256" pattern="[0-9a-fA-F]{64}" />
                </label>
                <label>
                  Size in bytes <input name="size" type="number" min={0} />
                </label>
              </>
            ) : (
              <label>
                File <input name="file" type="file" />
              </label>
            )}
            <label>
              Description <textarea name="description" rows={2} />
            </label>
            <label>
              Collected by <input name="collected_by" required defaultValue={collector} />
            </label>
            <label>
              Collected at <input name="collected_at" type="datetime-local" required defaultValue={nowLocal()} />
            </label>
            <label>
              Collected from <input name="collected_from" placeholder="Host, mailbox, or person" />
            </label>
            <label>
              Method <input name="method" placeholder="How it was collected" />
            </label>
            <div>
              <button type="submit" disabled={add.isPending}>
                Add evidence
              </button>{' '}
              {progress && <span role="status">{progress}</span>}
            </div>
            {add.error && <p role="alert">{add.error.message}</p>}
          </form>
        </details>
      )}
    </section>
  )
}
