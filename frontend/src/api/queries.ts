import { MutationCache, QueryClient, useQuery } from '@tanstack/react-query'
import { api } from './client'

export function useTenantConfig(slug: string, enabled = true) {
  return useQuery({
    queryKey: ['config', slug],
    queryFn: async () => {
      const { data, response } = await api.GET('/api/tenants/{slug}/config', {
        params: { path: { slug } },
      })
      if (!data) throw new Error(`Could not load settings (${response.status})`)
      return data
    },
    enabled,
  })
}

export function useMembers(slug: string) {
  return useQuery({
    queryKey: ['members', slug],
    queryFn: async () => {
      const { data, response } = await api.GET('/api/tenants/{slug}/members', {
        params: { path: { slug } },
      })
      if (!data) throw new Error(`Could not load members (${response.status})`)
      return data
    },
  })
}

/** Frames stay on screen side by side, so any change refreshes everything that is showing. */
export function createQueryClient(): QueryClient {
  const client: QueryClient = new QueryClient({
    mutationCache: new MutationCache({
      onSuccess: () => {
        void client.invalidateQueries()
      },
    }),
  })
  return client
}

/** A query function for an API call, which throws with a readable message when it fails. */
export function load<T>(what: string, call: () => Promise<{ data?: T; response: Response }>) {
  return async () => {
    const { data, response } = await call()
    if (data === undefined) throw new Error(`Could not load ${what} (${response.status})`)
    return data
  }
}
