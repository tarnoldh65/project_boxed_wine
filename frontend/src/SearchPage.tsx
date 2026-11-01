import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router'
import { api, describeError } from './api/client'
import { useTenant } from './tenant'

export function SearchPage() {
  const { slug } = useTenant()
  const queryClient = useQueryClient()
  const [params, setParams] = useSearchParams()
  const q = params.get('q') ?? ''
  const path = { params: { path: { slug } } }

  const results = useQuery({
    queryKey: ['search', slug, q],
    enabled: q !== '',
    queryFn: async () => {
      const { data, response } = await api.GET('/api/tenants/{slug}/search', {
        params: { path: { slug }, query: { q } },
      })
      if (!data) throw new Error(`Search failed (${response.status})`)
      return data
    },
  })
  const saved = useQuery({
    queryKey: ['searches', slug],
    queryFn: async () => {
      const { data, response } = await api.GET('/api/tenants/{slug}/searches', path)
      if (!data) throw new Error(`Could not load saved searches (${response.status})`)
      return data
    },
  })
  const change = useMutation({
    mutationFn: async (call: () => Promise<{ error?: unknown; response: Response }>) => {
      const { error, response } = await call()
      if (!response.ok) throw new Error(describeError(error, response.status))
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['searches', slug] }),
  })

  function run(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setParams({ q: String(new FormData(event.currentTarget).get('q')).trim() })
  }

  function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const name = String(new FormData(event.currentTarget).get('name'))
    event.currentTarget.reset()
    change.mutate(() =>
      api.POST('/api/tenants/{slug}/searches', { ...path, body: { name, query: q } }),
    )
  }

  return (
    <section>
      <h2>Search</h2>
      <form key={q} className="inline-form" onSubmit={run}>
        <input
          name="q"
          type="search"
          required
          maxLength={500}
          defaultValue={q}
          aria-label="Search terms"
          placeholder='Words, "a phrase", or -excluded'
        />
        <button type="submit">Search</button>
      </form>

      {!!saved.data?.length && (
        <ul className="saved-searches">
          {saved.data.map((s) => (
            <li key={s.id}>
              <Link to={`?q=${encodeURIComponent(s.query)}`}>{s.name}</Link>{' '}
              <button
                type="button"
                onClick={() =>
                  change.mutate(() =>
                    api.DELETE('/api/tenants/{slug}/searches/{search_id}', {
                      params: { path: { slug, search_id: s.id } },
                    }),
                  )
                }
              >
                Delete {s.name}
              </button>
            </li>
          ))}
        </ul>
      )}

      {results.isError && <p role="alert">{results.error.message}</p>}
      {results.data?.length === 0 && <p>No matches.</p>}
      {!!results.data?.length && (
        <>
          <ol className="results">
            {results.data.map((hit, i) => (
              <li key={i}>
                <Link to={`/t/${slug}/cases/${hit.case_number}`}>
                  #{hit.case_number} {hit.case_title}
                </Link>{' '}
                <span className="badge">{hit.kind}</span>
                <p>{hit.excerpt}</p>
              </li>
            ))}
          </ol>
          <form className="inline-form" onSubmit={save}>
            <input name="name" required maxLength={100} aria-label="Saved search name" placeholder="Name" />
            <button type="submit">Save search</button>
          </form>
        </>
      )}
      {(saved.error ?? change.error) && <p role="alert">{(saved.error ?? change.error)?.message}</p>}
    </section>
  )
}
