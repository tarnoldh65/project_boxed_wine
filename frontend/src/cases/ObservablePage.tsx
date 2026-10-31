import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router'
import { api } from '../api/client'
import { useTenant } from '../tenant'

export function ObservablePage() {
  const { slug } = useTenant()
  const observableId = useParams().observableId ?? ''
  const observable = useQuery({
    queryKey: ['observable', slug, observableId],
    queryFn: async () => {
      const { data, response } = await api.GET('/api/tenants/{slug}/observables/{observable_id}', {
        params: { path: { slug, observable_id: observableId } },
      })
      if (!data) {
        throw new Error(response.status === 404 ? 'Observable not found' : `Error ${response.status}`)
      }
      return data
    },
  })

  if (observable.isError) return <p role="alert">{observable.error.message}</p>
  if (!observable.data) return <p>Loading...</p>
  const { type, defanged, cases } = observable.data

  return (
    <article>
      <h2>
        {type}: <code>{defanged}</code>
      </h2>
      <p>
        Seen in {cases.length} case{cases.length === 1 ? '' : 's'}.
      </p>
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>Title</th>
            <th>State</th>
            <th>Severity</th>
            <th>Created</th>
          </tr>
        </thead>
        <tbody>
          {cases.map((c) => (
            <tr key={c.number}>
              <td>{c.number}</td>
              <td>
                <Link to={`/t/${slug}/cases/${c.number}`}>{c.title}</Link>
              </td>
              <td>{c.state}</td>
              <td>{c.severity}</td>
              <td>{new Date(c.created_at).toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </article>
  )
}
