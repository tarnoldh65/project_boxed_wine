import { useMutation, useQueryClient } from '@tanstack/react-query'
import { describeError, type TenantConfig } from '../api/client'

type PutResult = { data?: TenantConfig; error?: unknown; response: Response }

/** Save one configuration section and refresh the cached tenant configuration. */
export function useSaveSection<T>(slug: string, put: (body: T) => Promise<PutResult>) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (body: T) => {
      const { data, error, response } = await put(body)
      if (!data) throw new Error(describeError(error, response.status))
      return data
    },
    onSuccess: (config) => queryClient.setQueryData(['config', slug], config),
  })
}

export function updateAt<T>(list: T[], index: number, change: Partial<T>): T[] {
  return list.map((item, i) => (i === index ? { ...item, ...change } : item))
}

export function removeAt<T>(list: T[], index: number): T[] {
  return list.filter((_, i) => i !== index)
}

export function splitList(text: string): string[] {
  return text
    .split(',')
    .map((part) => part.trim())
    .filter(Boolean)
}
