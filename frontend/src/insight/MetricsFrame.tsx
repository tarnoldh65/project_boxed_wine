import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type Metric } from '../api/client'
import { load } from '../api/queries'
import { Skeleton } from '../Skeleton'
import { useTenant } from '../tenant'
import { BarChart } from './BarChart'
import { orderedBars } from './bars'

const WINDOWS = [30, 90, 365]

function hours(metric: Metric): string {
  if (metric.hours === null) return 'no data'
  const value = metric.hours >= 48 ? `${(metric.hours / 24).toFixed(1)} days` : `${metric.hours.toFixed(1)} hours`
  return `${value} (${metric.cases} cases)`
}

const percent = (rate: number | null) => (rate === null ? 'no data' : `${Math.round(rate * 100)}%`)

/** Largest first: categories have no natural order. */
function ranked(counts: Record<string, number>) {
  return orderedBars(counts, []).toSorted((a, b) => b.value - a.value)
}

export function MetricsFrame() {
  const { slug } = useTenant()
  const [days, setDays] = useState(90)
  const metrics = useQuery({
    queryKey: ['metrics', slug, days],
    queryFn: load('metrics', () =>
      api.GET('/api/tenants/{slug}/metrics', { params: { path: { slug }, query: { days } } }),
    ),
  })

  return (
    <div className="overview">
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
      {metrics.isError && <p role="alert">{metrics.error.message}</p>}
      {metrics.isPending && <Skeleton lines={6} />}
      {metrics.data && (
        <div className="charts">
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
          <BarChart caption="Cases by category" bars={ranked(metrics.data.by_category)} />
          <BarChart caption="Cases by ATT&CK technique" bars={ranked(metrics.data.by_technique)} />
        </div>
      )}
    </div>
  )
}
