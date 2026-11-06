import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'
import { api } from '../api/client'
import { load } from '../api/queries'
import { SlaBadge } from '../Badge'
import { Skeleton } from '../Skeleton'
import { useTenant } from '../tenant'

export function DeadlinesFrame() {
  const { slug } = useTenant()
  const clocks = useQuery({
    queryKey: ['clocks', slug],
    queryFn: load('notification clocks', () =>
      api.GET('/api/tenants/{slug}/clocks', { params: { path: { slug } } }),
    ),
  })

  if (clocks.isError) return <p role="alert">{clocks.error.message}</p>
  if (clocks.data?.length === 0) return <p className="empty">No notification clock is running.</p>
  if (!clocks.data) return <Skeleton />
  return (
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
            <td>
              <SlaBadge status={clock.status} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
