import createClient from 'openapi-fetch'
import type { components, paths } from './schema'

export type Me = components['schemas']['MeOut']
export type TenantAccess = components['schemas']['TenantAccess']
export type Token = components['schemas']['TokenOut']
export type TokenCreated = components['schemas']['TokenCreated']
export type TokenIn = components['schemas']['TokenIn']
export type TokenScope = TokenIn['scopes'][number]

export const api = createClient<paths>({
  baseUrl: window.location.origin,
  // Look up fetch per request so tests can stub it.
  fetch: (request) => fetch(request),
})
