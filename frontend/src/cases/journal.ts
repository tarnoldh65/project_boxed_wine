import { useMutation, useQueryClient } from '@tanstack/react-query'
import { describeError, type Case, type Member } from '../api/client'

type CaseCall = () => Promise<{ data?: Case; error?: unknown; response: Response }>

/** Run a case API call and show the case it returns. */
export function useCaseAction(slug: string, number: number) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (call: CaseCall) => {
      const { data, error, response } = await call()
      if (!data) throw new Error(describeError(error, response.status))
      return data
    },
    onSuccess: (updated) => queryClient.setQueryData(['case', slug, number], updated),
  })
}

/** A readable name for an audit-style actor such as "user:<id>". */
export function actorName(actor: string, members: Member[]): string {
  const [kind, id] = actor.split(':')
  if (kind === 'user') return members.find((m) => m.id === id)?.display_name ?? 'Former member'
  if (kind === 'token') return 'API token'
  return 'System'
}

export const PRIVILEGE_NOTICE = 'Attorney-Client Privileged / Prepared at Direction of Counsel'
