import { keepPreviousData, useInfiniteQuery, useMutation } from '@tanstack/react-query'
import { ArrowDown, ArrowUp, Plus } from 'lucide-react'
import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router'
import { api, describeError } from '../api/client'
import { useMembers, useTenantConfig } from '../api/queries'
import { Badge, SeverityBadge, SlaBadge } from '../Badge'
import { Skeleton } from '../Skeleton'
import { useTenant } from '../tenant'
import { severityTone } from '../tones'
import {
  isFiltered,
  NO_FILTER,
  OPEN_STATES,
  PAGE_SIZE,
  queueQuery,
  UNASSIGNED,
  type QueueFilter,
  type QueueSort,
  type SortKey,
} from './queue'

const COLUMNS: [SortKey, string][] = [
  ['number', '#'],
  ['title', 'Title'],
  ['state', 'State'],
  ['severity', 'Severity'],
  ['sla', 'SLA'],
  ['owner', 'Owner'],
  ['created', 'Created'],
]
const AGE_LABELS: Record<string, string> = {
  under_1_day: 'under 1 day old',
  '1_to_7_days': '1 to 7 days old',
  '7_to_30_days': '7 to 30 days old',
  over_30_days: 'over 30 days old',
}

const TYPING_PAUSE_MS = 300

function useDebounced(value: string): string {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), TYPING_PAUSE_MS)
    return () => clearTimeout(timer)
  }, [value])
  return settled
}

function FilterChip({ onClear, children }: { onClear: () => void; children: ReactNode }) {
  return (
    <button type="button" className="chip" onClick={onClear}>
      {children} <span aria-hidden>×</span>
    </button>
  )
}

export function CasesPage({
  filter,
  onFilter,
  activeCase,
}: {
  filter: QueueFilter
  onFilter: (filter: QueueFilter) => void
  activeCase?: number
}) {
  const { slug, permissions } = useTenant()
  const navigate = useNavigate()
  const [createdCount, setCreatedCount] = useState(0)
  const [sort, setSort] = useState<QueueSort>({ key: 'created', descending: true })
  const config = useTenantConfig(slug)
  const members = useMembers(slug)
  const applied = { ...filter, text: useDebounced(filter.text) }
  const cases = useInfiniteQuery({
    queryKey: ['cases', slug, applied, sort],
    initialPageParam: 0,
    queryFn: async ({ pageParam }) => {
      const { data, response } = await api.GET('/api/tenants/{slug}/cases', {
        params: { path: { slug }, query: queueQuery(applied, sort, pageParam) },
      })
      if (!data) throw new Error(`Could not load cases (${response.status})`)
      return data
    },
    getNextPageParam: (last, _, offset) => {
      const next = offset + PAGE_SIZE
      return next < last.total ? next : undefined
    },
    placeholderData: keepPreviousData,
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
    onSuccess: (created) => {
      // The queue stays on screen, so start the next case from a fresh, closed form.
      setCreatedCount((count) => count + 1)
      navigate(`/t/${slug}/cases/${created.number}`)
    },
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

  const shown = cases.data?.pages.flatMap((page) => page.cases) ?? []
  const total = cases.data?.pages.at(-1)?.total ?? 0

  function sortBy(key: SortKey) {
    setSort({ key, descending: sort.key === key ? !sort.descending : key === 'created' })
  }

  const change = (part: Partial<QueueFilter>) => onFilter({ ...filter, ...part })

  return (
    <div className="queue">
      <div className="queue-tools">
        <input
          type="search"
          aria-label="Filter cases"
          placeholder="Filter by title or number"
          value={filter.text}
          onChange={(e) => change({ text: e.target.value })}
        />
        <label>
          State{' '}
          <select value={filter.state} onChange={(e) => change({ state: e.target.value })}>
            <option value="">All</option>
            <option value={OPEN_STATES}>Open</option>
            {config.data?.states.map((s) => (
              <option key={s.key} value={s.key}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Severity{' '}
          <select value={filter.severity} onChange={(e) => change({ severity: e.target.value })}>
            <option value="">All</option>
            {config.data?.severity.levels.map((l) => (
              <option key={l.key} value={l.key}>
                {l.key}
              </option>
            ))}
          </select>
        </label>
        <label>
          Owner{' '}
          <select value={filter.owner} onChange={(e) => change({ owner: e.target.value })}>
            <option value="">All</option>
            <option value={UNASSIGNED}>Unassigned</option>
            {members.data?.map((m) => (
              <option key={m.id} value={m.id}>
                {m.display_name}
              </option>
            ))}
          </select>
        </label>
        {permissions.includes('cases:write') && config.data && (
          <details key={createdCount} className="new-case">
            <summary className="button">
              <Plus aria-hidden size={16} /> New case
            </summary>
            <form className="stack" onSubmit={onSubmit}>
              <label>
                Type{' '}
                <select name="template" required>
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
      </div>

      {isFiltered(filter) && (
        <p className="queue-active">
          {filter.sla && (
            <FilterChip onClear={() => change({ sla: '' })}>SLA {filter.sla}</FilterChip>
          )}
          {filter.age && (
            <FilterChip onClear={() => change({ age: '' })}>{AGE_LABELS[filter.age]}</FilterChip>
          )}
          <button type="button" className="ghost" onClick={() => onFilter(NO_FILTER)}>
            Clear filters
          </button>
          <span className="note-meta">
            {total} {total === 1 ? 'case' : 'cases'}
          </span>
        </p>
      )}

      {cases.isError && <p role="alert">{cases.error.message}</p>}
      {cases.isPending && <Skeleton lines={8} />}
      {cases.data && total === 0 && (
        <p className="empty">{isFiltered(filter) ? 'No cases match these filters.' : 'No cases yet.'}</p>
      )}
      {shown.length > 0 && (
        <table className="queue-table">
          <thead>
            <tr>
              {COLUMNS.map(([key, label]) => (
                <th
                  key={key}
                  className={`col-${key}`}
                  aria-sort={sort.key !== key ? undefined : sort.descending ? 'descending' : 'ascending'}
                >
                  <button type="button" className="sort" onClick={() => sortBy(key)}>
                    {label}
                    {sort.key === key &&
                      (sort.descending ? (
                        <ArrowDown aria-hidden size={12} />
                      ) : (
                        <ArrowUp aria-hidden size={12} />
                      ))}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((c) => (
              <tr key={c.number} className={c.number === activeCase ? 'selected' : undefined}>
                <td className={`col-number marker-${severityTone(c.severity)}`}>{c.number}</td>
                <td className="col-title">
                  <Link
                    to={`/t/${slug}/cases/${c.number}`}
                    aria-current={c.number === activeCase ? 'true' : undefined}
                  >
                    {c.title}
                  </Link>
                  {c.restricted && ' (restricted)'}
                  {c.major_incident_at && ' (major incident)'}
                </td>
                <td className="col-state">
                  <Badge>{stateName(c.state)}</Badge>
                </td>
                <td className="col-severity">
                  <SeverityBadge priority={c.priority} severity={c.severity} />
                </td>
                <td className="col-sla">{c.sla_status && <SlaBadge status={c.sla_status} />}</td>
                <td className="col-owner">{ownerName(c.owner_id)}</td>
                <td className="col-created">{new Date(c.created_at).toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {cases.hasNextPage && (
        <p className="queue-more">
          <span className="note-meta">
            Showing {shown.length} of {total}
          </span>{' '}
          <button type="button" disabled={cases.isFetchingNextPage} onClick={() => cases.fetchNextPage()}>
            Show more
          </button>
        </p>
      )}
    </div>
  )
}
