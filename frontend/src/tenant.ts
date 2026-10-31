import { useOutletContext } from 'react-router'
import type { TenantAccess } from './api/client'

/** The tenant of the current route, provided by the tenant layout. */
export function useTenant(): TenantAccess {
  return useOutletContext<TenantAccess>()
}
