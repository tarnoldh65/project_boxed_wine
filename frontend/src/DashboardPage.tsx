import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router'
import { api, type Metric } from './api/client'
import { useMembers, useTenantConfig } from './api/queries'
import { useTenant } from './tenant'

const AGES = [
  ['under_1_day', 'Under 1 day'],
  ['1_to_7_days', '1 to 7 days'],
  ['7_to_30_days', '7 to 30 days'],
  ['over_30_days', 'Over 30 days'],
] as const
const WINDOWS = [30, 90, 365]

function hours(metric: Metric): string {
  if (metric.hours === null) return 'no data'
  const value = metric.hours >= 48 ? `${(metric.hours / 24).toFixed(1)} days` : `${metric.hours.toFixed(1)} hours`
  return `${value} (${metric.cases} cases)`
}

const percent = (rate: number | null) => (rate === null ? 'no data' : `${Math.round(rate * 100)}%`)

function Counts({ caption, counts, label = (key) => key }: {
  caption: string
  counts: Record<string, number>
  label?: (key: string) => string
}) {
  const entries = Object.entries(counts)
  return (
    <table>
      <caption>{caption}</caption>
      <tbody>
        {entries.length === 0 && (
          <tr>
            <td>None</td>
          </tr>
        )}
        {entries.map(([key, count]) => (
          <tr key={key}>
            <th scope="row">{label(key)}</th>
            <td>{count}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function load<T>(what: string, call: () => Promise<{ data?: T; response: Response }>) {
  return async () => {
    const { data, response } = await call()
    if (data === undefined) throw new Error(`Could not load ${what} (${response.status})`)
    return data
  }
}

export function DashboardPage() {
  const { slug } = useTenant()
  const [days, setDays] = useState(90)
  const config = useTenantConfig(slug)
  const members = useMembers(slug)
  const path = { params: { path: { slug } } }
  const board = useQuery({
    queryKey: ['dashboard', slug],
    queryFn: load('the dashboard', () => api.GET('/api/tenants/{slug}/dashboard', path)),
  })
  const clocks = useQuery({
    queryKey: ['clocks', slug],
    queryFn: load('notification clocks', () => api.GET('/api/tenants/{slug}/clocks', path)),
  })
  const metrics = useQuery({
    queryKey: ['metrics', slug, days],
    queryFn: load('metrics', () =>
      api.GET('/api/tenants/{slug}/metrics', { params: { path: { slug }, query: { days } } }),
    ),
  })

  const stateName = (key: string) => config.data?.states.find((s) => s.key === key)?.name ?? key
  const ownerName = (id: string | null) =>
    id ? (members.data?.find((m) => m.id === id)?.display_name ?? 'Former member') : 'Unassigned'
  const error = board.error ?? clocks.error ?? metrics.error
  const aging = board.data?.aging ?? {}

  return (
    <section className="dashboard">
      <h2>Dashboard</h2>
      {error && <p role="alert">{error.message}</p>}
      {board.data && (
        <div className="panels">
          <Counts caption="Open cases by severity" counts={board.data.open_by_severity} />
          <Counts caption="Open cases by state" counts={board.data.open_by_state} label={stateName} />
          <Counts caption="Open cases by SLA status" counts={board.data.sla_status} />
          <Counts
            caption="Open cases by age"
            counts={Object.fromEntries(
              AGES.filter(([key]) => aging[key]).map(([key, label]) => [label, aging[key]]),
            )}
          />
          <table>
            <caption>Analyst workload</caption>
            <tbody>
              {board.data.workload.map((w) => (
                <tr key={w.owner_id ?? 'none'}>
                  <th scope="row">{ownerName(w.owner_id)}</th>
                  <td>{w.open}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p>
            SLA compliance, cases closed in the last 30 days: {percent(board.data.sla_compliance)}
            <br />
            Open post-incident review actions: {board.data.open_pir_actions}
          </p>
        </div>
      )}

      <h3>Notification deadlines</h3>
      {clocks.data?.length === 0 && <p>No notification clock is running.</p>}
      {!!clocks.data?.length && (
        <table>
          <thead>
            <tr>
              <th>Case</th>
              <th>Obligation</th>
              <th>Due</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {clocks.data.map((clock) => (
              <tr key={clock.id}>
                <td>
                  <Link to={`/t/${slug}/cases/${clock.case_number}`}>#{clock.case_number}</Link>
                </td>
                <td>{clock.name}</td>
                <td>{new Date(clock.due_at).toLocaleString()}</td>
                <td className={`sla-${clock.status}`}>{clock.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h3>Metrics</h3>
      <label>
        Cases opened in the last{' '}
        <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {WINDOWS.map((d) => (
            <option key={d} value={d}>
              {d} days
            </option>
          ))}
        </select>
      </label>
      {metrics.data && (
        <div className="panels">
          <table>
            <caption>Response times ({metrics.data.cases} cases)</caption>
            <tbody>
              {(
                [
                  ['Mean time to detect', metrics.data.mttd],
                  ['Mean time to acknowledge', metrics.data.mtta],
                  ['Mean time to contain', metrics.data.mttc],
                  ['Mean time to resolve', metrics.data.mttr],
                  ['Dwell time', metrics.data.dwell_time],
                ] as const
              ).map(([label, metric]) => (
                <tr key={label}>
                  <th scope="row">{label}</th>
                  <td>{hours(metric)}</td>
                </tr>
              ))}
              <tr>
                <th scope="row">False-positive rate</th>
                <td>
                  {percent(metrics.data.false_positive_rate)} of {metrics.data.closed} closed
                </td>
              </tr>
              <tr>
                <th scope="row">Recurrence</th>
                <td>
                  {metrics.data.recurring} cases ({percent(metrics.data.recurrence_rate)})
                </td>
              </tr>
            </tbody>
          </table>
          <Counts caption="Cases by category" counts={metrics.data.by_category} />
          <Counts caption="Cases by ATT&CK technique" counts={metrics.data.by_technique} />
        </div>
      )}
    </section>
  )
}
