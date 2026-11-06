import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { load, useMembers, useTenantConfig } from '../api/queries'
import {
  openCasesFilter,
  sameFilter,
  UNASSIGNED,
  type QueueFilter,
} from '../cases/queue'
import { Skeleton } from '../Skeleton'
import { useTenant } from '../tenant'
import { severityTone, slaTone } from '../tones'
import { BarChart } from './BarChart'
import { orderedBars, type Bar } from './bars'

const AGES = [
  ['under_1_day', 'Under 1 day'],
  ['1_to_7_days', '1 to 7 days'],
  ['7_to_30_days', '7 to 30 days'],
  ['over_30_days', 'Over 30 days'],
] as const
const SLA_ORDER = ['breached', 'warning', 'late', 'pending', 'met', 'none']

const percent = (rate: number | null) => (rate === null ? 'no data' : `${Math.round(rate * 100)}%`)

function Tile({
  label,
  value,
  selected,
  onSelect,
}: {
  label: string
  value: string | number
  selected?: boolean
  onSelect?: () => void
}) {
  const content = (
    <>
      <span className="tile-label">{label}</span>{' '}
      <span className="tile-value">{value}</span>
    </>
  )
  if (!onSelect) return <div className="tile">{content}</div>
  return (
    <button type="button" className="tile" aria-pressed={selected} onClick={onSelect}>
      {content}
    </button>
  )
}

export function OverviewFrame({
  filter,
  onFilter,
  onMetrics,
}: {
  filter: QueueFilter
  onFilter: (part: Partial<QueueFilter>) => void
  onMetrics: () => void
}) {
  const { slug } = useTenant()
  const config = useTenantConfig(slug)
  const members = useMembers(slug)
  const board = useQuery({
    queryKey: ['dashboard', slug],
    queryFn: load('the dashboard', () =>
      api.GET('/api/tenants/{slug}/dashboard', { params: { path: { slug } } }),
    ),
  })

  if (board.isError) return <p role="alert">{board.error.message}</p>
  if (!board.data) return <Skeleton lines={6} />
  const data = board.data

  const stateName = (key: string) => config.data?.states.find((s) => s.key === key)?.name ?? key
  const ownerName = (id: string) =>
    members.data?.find((m) => m.id === id)?.display_name ?? 'Former member'
  const active = (part: Partial<QueueFilter>) => sameFilter(filter, openCasesFilter(part))
  const mark = (field: keyof QueueFilter, bars: Bar[]) =>
    bars.map((bar) => ({ ...bar, selected: active({ [field]: bar.key }) }))
  const open = Object.values(data.open_by_state).reduce((sum, count) => sum + count, 0)
  const unassigned = data.workload.find((w) => w.owner_id === null)?.open ?? 0

  return (
    <div className="overview">
      <div className="tiles">
        <Tile label="Open cases" value={open} selected={active({})} onSelect={() => onFilter({})} />
        <Tile
          label="SLA breached"
          value={data.sla_status.breached ?? 0}
          selected={active({ sla: 'breached' })}
          onSelect={() => onFilter({ sla: 'breached' })}
        />
        <Tile
          label="Unassigned"
          value={unassigned}
          selected={active({ owner: UNASSIGNED })}
          onSelect={() => onFilter({ owner: UNASSIGNED })}
        />
        <Tile label="SLA compliance, 30 days" value={percent(data.sla_compliance)} onSelect={onMetrics} />
        <Tile label="Open review actions" value={data.open_pir_actions} />
      </div>
      <div className="charts">
        <BarChart
          caption="Open cases by severity"
          bars={mark(
            'severity',
            orderedBars(data.open_by_severity, config.data?.severity.levels.map((l) => l.key) ?? []),
          ).map((bar) => ({ ...bar, tone: severityTone(bar.key) }))}
          onSelect={(severity) => onFilter({ severity })}
        />
        <BarChart
          caption="Open cases by state"
          bars={mark(
            'state',
            orderedBars(data.open_by_state, config.data?.states.map((s) => s.key) ?? [], stateName),
          )}
          onSelect={(state) => onFilter({ state })}
        />
        <BarChart
          caption="Open cases by SLA status"
          bars={mark('sla', orderedBars(data.sla_status, SLA_ORDER)).map((bar) => ({
            ...bar,
            tone: slaTone(bar.key),
          }))}
          onSelect={(sla) => onFilter({ sla })}
        />
        <BarChart
          caption="Open cases by age"
          bars={mark(
            'age',
            AGES.filter(([key]) => data.aging[key]).map(([key, label]) => ({
              key,
              label,
              value: data.aging[key],
            })),
          )}
          onSelect={(age) => onFilter({ age })}
        />
        <BarChart
          caption="Analyst workload"
          bars={mark(
            'owner',
            data.workload.map((w) => ({
              key: w.owner_id ?? UNASSIGNED,
              label: w.owner_id ? ownerName(w.owner_id) : 'Unassigned',
              value: w.open,
            })),
          )}
          onSelect={(owner) => onFilter({ owner })}
        />
      </div>
    </div>
  )
}
