import { useQuery } from '@tanstack/react-query'
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
