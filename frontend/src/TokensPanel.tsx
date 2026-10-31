import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { api, type Token, type TokenCreated, type TokenIn, type TokenScope } from './api/client'

const SCOPES: TokenScope[] = ['cases:read', 'cases:write']

function status(token: Token): string {
  if (token.revoked_at) return 'Revoked'
  if (new Date(token.expires_at) <= new Date()) return 'Expired'
  return 'Active'
}

export function TokensPanel({ slug }: { slug: string }) {
  const queryClient = useQueryClient()
  const [created, setCreated] = useState<TokenCreated | null>(null)
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['tokens', slug] })

  const tokens = useQuery({
    queryKey: ['tokens', slug],
    queryFn: async () => {
      const { data, response } = await api.GET('/api/tenants/{slug}/tokens', {
        params: { path: { slug } },
      })
      if (!data) throw new Error(`Could not load tokens (${response.status})`)
      return data
    },
  })

  const create = useMutation({
    mutationFn: async (body: TokenIn) => {
      const { data, response } = await api.POST('/api/tenants/{slug}/tokens', {
        params: { path: { slug } },
        body,
      })
      if (!data) throw new Error(`Could not create token (${response.status})`)
      return data
    },
    onSuccess: (token) => {
      setCreated(token)
      return refresh()
    },
  })

  const revoke = useMutation({
    mutationFn: (tokenId: string) =>
      api.DELETE('/api/tenants/{slug}/tokens/{token_id}', {
        params: { path: { slug, token_id: tokenId } },
      }),
    onSuccess: refresh,
  })

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    create.mutate({
      name: String(form.get('name')),
      scopes: form.getAll('scopes') as TokenScope[],
      expires_in_days: Number(form.get('expires_in_days')),
    })
  }

  return (
    <section>
      <h2>API tokens</h2>
      {created && (
        <div role="status" className="notice">
          Copy this token now. It will not be shown again: <code>{created.token}</code>
        </div>
      )}
      <form className="stack" onSubmit={onSubmit}>
        <label>
          Name <input name="name" required maxLength={100} />
        </label>
        <fieldset>
          <legend>Scopes</legend>
          {SCOPES.map((scope) => (
            <label key={scope}>
              <input
                type="checkbox"
                name="scopes"
                value={scope}
                defaultChecked={scope === 'cases:read'}
              />{' '}
              {scope}
            </label>
          ))}
        </fieldset>
        <label>
          Expires in days{' '}
          <input name="expires_in_days" type="number" min={1} max={365} defaultValue={90} required />
        </label>
        <div>
          <button type="submit" disabled={create.isPending}>
            Create token
          </button>
        </div>
        {create.isError && <p role="alert">{create.error.message}</p>}
      </form>
      {tokens.isError && <p role="alert">{tokens.error.message}</p>}
      {tokens.data && (
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Prefix</th>
              <th>Scopes</th>
              <th>Expires</th>
              <th>Status</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {tokens.data.map((token) => (
              <tr key={token.id}>
                <td>{token.name}</td>
                <td>
                  <code>{token.prefix}</code>
                </td>
                <td>{token.scopes.join(', ')}</td>
                <td>{new Date(token.expires_at).toLocaleDateString()}</td>
                <td>{status(token)}</td>
                <td>
                  {status(token) === 'Active' && (
                    <button type="button" onClick={() => revoke.mutate(token.id)}>
                      Revoke {token.name}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
