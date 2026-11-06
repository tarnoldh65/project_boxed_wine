import type { paths } from '../api/schema'

export const OPEN_STATES = '*open'
export const UNASSIGNED = '*unassigned'
export const PAGE_SIZE = 100

type QueueQuery = NonNullable<paths['/api/tenants/{slug}/cases']['get']['parameters']['query']>
export type SortKey = NonNullable<QueueQuery['sort']>

export interface QueueSort {
  key: SortKey
  descending: boolean
}

export interface QueueFilter {
  text: string
  /** A state key, OPEN_STATES, or empty for all. */
  state: string
  severity: string
  /** A member id, UNASSIGNED, or empty for all. */
  owner: string
  /** An SLA status, or "none" for a case with no SLA clock. */
  sla: string
  /** An age bucket name from the dashboard. */
  age: string
}

export const NO_FILTER: QueueFilter = { text: '', state: '', severity: '', owner: '', sla: '', age: '' }

/** The queue filter behind a dashboard figure, which counts open cases only. */
export function openCasesFilter(change: Partial<QueueFilter> = {}): QueueFilter {
  return { ...NO_FILTER, state: OPEN_STATES, ...change }
}

export function sameFilter(a: QueueFilter, b: QueueFilter): boolean {
  return JSON.stringify(a) === JSON.stringify(b)
}

export function isFiltered(filter: QueueFilter): boolean {
  return !sameFilter(filter, NO_FILTER)
}

/** The case list query for a filter, an order, and a page. */
export function queueQuery(filter: QueueFilter, sort: QueueSort, offset: number): QueueQuery {
  const { text, state, severity, owner, sla, age } = filter
  return {
    ...(text.trim() && { text: text.trim() }),
    ...(state === OPEN_STATES ? { open_only: true } : state && { state }),
    ...(severity && { severity }),
    ...(owner === UNASSIGNED ? { unassigned: true } : owner && { owner }),
    ...(sla && { sla: sla as QueueQuery['sla'] }),
    ...(age && { age: age as QueueQuery['age'] }),
    sort: sort.key,
    descending: sort.descending,
    limit: PAGE_SIZE,
    offset,
  }
}
