import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type Me } from '../api/client'

/** The one copy of a case that all of its frames show. */
export function useCase(slug: string, number: number) {
  return useQuery({
    queryKey: ['case', slug, number],
    queryFn: async () => {
      const { data, response } = await api.GET('/api/tenants/{slug}/cases/{number}', {
        params: { path: { slug, number } },
      })
      if (!data) {
        throw new Error(response.status === 404 ? 'Case not found' : `Error ${response.status}`)
      }
      return data
    },
  })
}

/** The display name evidence collection defaults to. */
export function useCollector(): string {
  return useQueryClient().getQueryData<Me>(['me'])?.display_name ?? ''
}

export interface SplitItems {
  alerts: string[]
  assets: string[]
  observables: string[]
}

const NOTHING: SplitItems = { alerts: [], assets: [], observables: [] }

export interface SplitSelection extends SplitItems {
  choose: (kind: keyof SplitItems, ids: string[]) => void
  clear: () => void
}

/** Items picked across a case's frames to split into a new case; a new case starts empty. */
export function useSplitSelection(caseNumber?: number): SplitSelection {
  const [selected, setSelected] = useState({ caseNumber, items: NOTHING })
  const items = selected.caseNumber === caseNumber ? selected.items : NOTHING
  return {
    ...items,
    choose: (kind, ids) => setSelected({ caseNumber, items: { ...items, [kind]: ids } }),
    clear: () => setSelected({ caseNumber, items: NOTHING }),
  }
}
