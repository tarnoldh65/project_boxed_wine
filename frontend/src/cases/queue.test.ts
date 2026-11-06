import { expect, test } from 'vitest'
import {
  isFiltered,
  NO_FILTER,
  OPEN_STATES,
  openCasesFilter,
  PAGE_SIZE,
  queueQuery,
  sameFilter,
  UNASSIGNED,
  type QueueFilter,
  type QueueSort,
} from './queue'

const SORT: QueueSort = { key: 'created', descending: true }
const PAGE = { sort: 'created', descending: true, limit: PAGE_SIZE, offset: 0 }

test.each<[string, Partial<QueueFilter>, object]>([
  ['nothing', {}, {}],
  ['trimmed text', { text: ' phish ' }, { text: 'phish' }],
  ['blank text', { text: '  ' }, {}],
  ['a state', { state: 'triage' }, { state: 'triage' }],
  ['open states', { state: OPEN_STATES }, { open_only: true }],
  ['severity', { severity: 'high' }, { severity: 'high' }],
  ['an owner', { owner: 'u1' }, { owner: 'u1' }],
  ['no owner', { owner: UNASSIGNED }, { unassigned: true }],
  ['SLA and age', { sla: 'breached', age: '1_to_7_days' }, { sla: 'breached', age: '1_to_7_days' }],
])('asks the server for %s', (_, filter, expected) => {
  expect(queueQuery({ ...NO_FILTER, ...filter }, SORT, 0)).toEqual({ ...expected, ...PAGE })
})

test('passes the order and the page', () => {
  expect(queueQuery(NO_FILTER, { key: 'severity', descending: false }, 200)).toEqual({
    ...PAGE,
    sort: 'severity',
    descending: false,
    offset: 200,
  })
})

test('a dashboard figure means open cases, plus its own condition', () => {
  expect(openCasesFilter({ severity: 'high' })).toEqual({ ...NO_FILTER, state: OPEN_STATES, severity: 'high' })
  expect(sameFilter(openCasesFilter(), openCasesFilter())).toBe(true)
  expect(sameFilter(openCasesFilter(), NO_FILTER)).toBe(false)
  expect(isFiltered(NO_FILTER)).toBe(false)
  expect(isFiltered({ ...NO_FILTER, text: 'x' })).toBe(true)
})
