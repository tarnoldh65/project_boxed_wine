import createClient from 'openapi-fetch'
import type { components, paths } from './schema'

type Schemas = components['schemas']
export type Me = Schemas['MeOut']
export type TenantAccess = Schemas['TenantAccess']
export type Token = Schemas['TokenOut']
export type TokenCreated = Schemas['TokenCreated']
export type TokenIn = Schemas['TokenIn']
export type TokenScope = TokenIn['scopes'][number]
export type TenantConfig = Schemas['TenantConfig']
export type State = Schemas['State']
export type CustomField = Schemas['CustomField']
export type FieldType = Schemas['FieldType']
export type Severity = Schemas['Severity']
export type SeverityLevel = Schemas['SeverityLevel']
export type EscalationRule = Schemas['EscalationRule']
export type Trigger = Schemas['Trigger']

export const api = createClient<paths>({
  baseUrl: window.location.origin,
  // Look up fetch per request so tests can stub it.
  fetch: (request) => fetch(request),
})

/** Turn an API error body into a readable message, including each validation problem. */
export function describeError(error: unknown, status: number): string {
  const detail = (error as { detail?: unknown } | undefined)?.detail
  if (!Array.isArray(detail) || detail.length === 0) return `Request failed (${status})`
  return (detail as Schemas['ValidationError'][])
    .map((d) => {
      const where = d.loc.filter((part) => part !== 'body').join('.')
      const message = d.msg.replace(/^Value error, /, '')
      return where ? `${where}: ${message}` : message
    })
    .join('; ')
}
