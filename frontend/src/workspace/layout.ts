import { moveElement, verticalCompactor, type Layout, type LayoutItem } from 'react-grid-layout'

export type Breakpoint = 'lg' | 'md' | 'sm' | 'xs'
export type FrameId =
  | 'queue'
  | 'overview'
  | 'deadlines'
  | 'metrics'
  | 'search'
  | 'case'
  | 'case-details'
  | 'case-tasks'
  | 'case-timeline'
  | 'case-notes'
  | 'case-comments'
  | 'case-observables'
  | 'case-evidence'
  | 'case-assets'
  | 'case-alerts'
  | 'case-regulatory'
  | 'case-pir'
  | 'case-reports'
  | 'custody'
  | 'observable'
export type Move = 'left' | 'right' | 'up' | 'down' | 'wider' | 'narrower' | 'taller' | 'shorter'

export interface WorkspaceState {
  open: FrameId[]
  collapsed: FrameId[]
  layouts: Record<Breakpoint, Layout>
}

interface FrameSpec {
  title: string
  /** Default and minimum width on the 12-column grid, and default and minimum rows. */
  w: number
  h: number
  minW: number
  minH: number
  /** Open at the top right, pushing what is there down, rather than in the first free space. */
  prominent?: boolean
}

export const BREAKPOINTS: Record<Breakpoint, number> = { lg: 1200, md: 900, sm: 600, xs: 0 }
export const COLS: Record<Breakpoint, number> = { lg: 12, md: 8, sm: 6, xs: 1 }
const BREAKPOINT_NAMES = Object.keys(BREAKPOINTS) as Breakpoint[]

export const FRAMES: Record<FrameId, FrameSpec> = {
  queue: { title: 'Case queue', w: 5, h: 20, minW: 3, minH: 6 },
  overview: { title: 'Overview', w: 7, h: 12, minW: 3, minH: 6 },
  deadlines: { title: 'Notification deadlines', w: 7, h: 8, minW: 3, minH: 4 },
  metrics: { title: 'Metrics', w: 7, h: 12, minW: 3, minH: 6 },
  search: { title: 'Search', w: 7, h: 10, minW: 3, minH: 6 },
  case: { title: 'Case', w: 7, h: 18, minW: 4, minH: 6, prominent: true },
  'case-details': { title: 'Details', w: 7, h: 14, minW: 4, minH: 6 },
  'case-tasks': { title: 'Tasks', w: 7, h: 9, minW: 3, minH: 5 },
  'case-timeline': { title: 'Timeline', w: 7, h: 10, minW: 3, minH: 5 },
  'case-notes': { title: 'Notes', w: 5, h: 12, minW: 3, minH: 5 },
  'case-comments': { title: 'Comments', w: 5, h: 10, minW: 3, minH: 5 },
  'case-observables': { title: 'Observables', w: 5, h: 12, minW: 3, minH: 5 },
  'case-evidence': { title: 'Evidence', w: 5, h: 12, minW: 3, minH: 5 },
  'case-assets': { title: 'Assets', w: 5, h: 8, minW: 3, minH: 4 },
  'case-alerts': { title: 'Alerts', w: 5, h: 8, minW: 3, minH: 4 },
  'case-regulatory': { title: 'Regulatory', w: 7, h: 12, minW: 3, minH: 5 },
  'case-pir': { title: 'Post-incident review', w: 7, h: 14, minW: 3, minH: 5 },
  'case-reports': { title: 'Reports', w: 5, h: 6, minW: 3, minH: 3 },
  custody: { title: 'Chain of custody', w: 5, h: 10, minW: 3, minH: 5 },
  observable: { title: 'Observable', w: 5, h: 10, minW: 3, minH: 6 },
}
const FRAME_IDS = Object.keys(FRAMES) as FrameId[]

/** Frames that are always available from the launcher; the rest open from a selection. */
export const LAUNCHABLE: FrameId[] = ['queue', 'overview', 'deadlines', 'metrics', 'search']
const DEFAULT_FRAMES: FrameId[] = ['queue', 'overview', 'deadlines']

/** The parts of a case, in the order its summary offers them. */
export const CASE_FRAMES: FrameId[] = [
  'case-details',
  'case-tasks',
  'case-timeline',
  'case-notes',
  'case-comments',
  'case-observables',
  'case-evidence',
  'case-assets',
  'case-alerts',
  'case-regulatory',
  'case-pir',
  'case-reports',
]

const STORAGE_VERSION = 3
/** Version 2 opened a frame per case section; version 3 shows them as tabs of the case. */
const TABBED_CASES_VERSION = 3
const COLLAPSED_ROWS = 1

function storageKey(userId: string, slug: string): string {
  return `casemgr.layout.${userId}.${slug}`
}

/** A frame's width and minimum width in `cols` columns. Narrow grids give every frame the full width. */
function widths(id: FrameId, cols: number): { w: number; minW: number } {
  const { w, minW } = FRAMES[id]
  if (cols < COLS.md) return { w: cols, minW: Math.min(cols, 2) }
  return { w: Math.round((w * cols) / 12), minW: Math.round((minW * cols) / 12) }
}

/** Position and size only: the grid's own bookkeeping fields stay out of saved layouts. */
function plain(layout: Layout): Layout {
  return layout.map(({ i, x, y, w, h }) => ({ i, x, y, w, h }))
}

function overlaps(a: LayoutItem, b: LayoutItem): boolean {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h
}

function place(layout: Layout, id: FrameId, cols: number): Layout {
  const { w } = widths(id, cols)
  const { h, prominent } = FRAMES[id]
  if (prominent) {
    return plain(verticalCompactor.compact([...layout, { i: id, x: cols - w, y: -1, w, h }], cols))
  }
  for (let y = 0; ; y++) {
    for (let x = 0; x + w <= cols; x++) {
      const item = { i: id, x, y, w, h }
      if (!layout.some((other) => overlaps(item, other))) return [...layout, item]
    }
  }
}

function eachBreakpoint(build: (breakpoint: Breakpoint) => Layout): Record<Breakpoint, Layout> {
  return Object.fromEntries(BREAKPOINT_NAMES.map((b) => [b, build(b)])) as Record<Breakpoint, Layout>
}

export function openFrame(state: WorkspaceState, id: FrameId): WorkspaceState {
  const collapsed = state.collapsed.filter((frame) => frame !== id)
  if (state.open.includes(id)) {
    return collapsed.length === state.collapsed.length ? state : { ...state, collapsed }
  }
  return {
    open: [...state.open, id],
    collapsed,
    layouts: eachBreakpoint((b) => place(state.layouts[b], id, COLS[b])),
  }
}

export function openCase(state: WorkspaceState): WorkspaceState {
  return openFrame(state, 'case')
}

/** Columns two items share. */
function sharesColumns(a: LayoutItem, b: LayoutItem): boolean {
  return a.x < b.x + b.w && b.x < a.x + a.w
}

/** A case section in its own frame, docked right under the case and pushing what is below down. */
export function popOut(state: WorkspaceState, id: FrameId): WorkspaceState {
  if (state.open.includes(id)) return openFrame(state, id)
  return {
    open: [...state.open, id],
    collapsed: state.collapsed.filter((frame) => frame !== id),
    layouts: eachBreakpoint((b) => {
      const anchor = state.layouts[b].find((item) => item.i === 'case')
      if (!anchor) return place(state.layouts[b], id, COLS[b])
      const item = { i: id, x: anchor.x, y: anchor.y + anchor.h, w: anchor.w, h: FRAMES[id].h }
      const below = state.layouts[b].map((other) =>
        other.y >= item.y && sharesColumns(other, item) ? { ...other, y: other.y + item.h } : other,
      )
      return plain(verticalCompactor.compact([...below, item], COLS[b]))
    }),
  }
}

export function closeCase(state: WorkspaceState): WorkspaceState {
  return ['case', 'custody', ...CASE_FRAMES].reduce<WorkspaceState>(
    (current, id) => closeFrame(current, id as FrameId),
    state,
  )
}

export function closeFrame(state: WorkspaceState, id: FrameId): WorkspaceState {
  return {
    open: state.open.filter((frame) => frame !== id),
    collapsed: state.collapsed.filter((frame) => frame !== id),
    layouts: eachBreakpoint((b) => state.layouts[b].filter((item) => item.i !== id)),
  }
}

export function toggleCollapsed(state: WorkspaceState, id: FrameId): WorkspaceState {
  const collapsed = state.collapsed.includes(id)
    ? state.collapsed.filter((frame) => frame !== id)
    : [...state.collapsed, id]
  return { ...state, collapsed }
}

export function defaultWorkspace(): WorkspaceState {
  const empty = { open: [], collapsed: [], layouts: eachBreakpoint(() => []) }
  return DEFAULT_FRAMES.reduce(openFrame, empty)
}

/** What the grid shows: the visible frames, with collapsed ones reduced to their title bar. */
export function gridLayouts(
  state: WorkspaceState,
  visible: FrameId[],
): Record<Breakpoint, Layout> {
  return eachBreakpoint((b) =>
    state.layouts[b]
      .filter((item) => visible.includes(item.i as FrameId))
      .map((item) => {
        const id = item.i as FrameId
        const { minW } = widths(id, COLS[b])
        return state.collapsed.includes(id)
          ? { ...item, minW, h: COLLAPSED_ROWS, minH: COLLAPSED_ROWS, isResizable: false }
          : { ...item, minW, minH: FRAMES[id].minH }
      }),
  )
}

/** Frames top to bottom, then left to right: the order the eye and the keyboard should follow. */
export function readingOrder(layout: Layout): FrameId[] {
  return layout.toSorted((a, b) => a.y - b.y || a.x - b.x).map((item) => item.i as FrameId)
}

/** Take positions from the grid, keeping hidden frames and the full height of collapsed ones. */
export function mergeGrid(
  state: WorkspaceState,
  grid: Partial<Record<string, Layout>>,
): WorkspaceState {
  const layouts = eachBreakpoint((b) =>
    state.layouts[b].map((item) => {
      const shown = grid[b]?.find((candidate) => candidate.i === item.i)
      if (!shown) return item
      const h = state.collapsed.includes(item.i as FrameId) ? item.h : shown.h
      return { i: item.i, x: shown.x, y: shown.y, w: shown.w, h }
    }),
  )
  return JSON.stringify(layouts) === JSON.stringify(state.layouts) ? state : { ...state, layouts }
}

/** The grid position after a keyboard move, using the same collision rules as dragging. */
function moved(layout: Layout, id: FrameId, move: Move, cols: number): Layout {
  const item = layout.find((candidate) => candidate.i === id)
  if (!item) return layout
  const others = layout.filter((other) => other !== item)
  const sameColumns = others.filter((o) => item.x < o.x + o.w && o.x < item.x + item.w)
  const resized = (change: Partial<LayoutItem>) =>
    verticalCompactor.compact(
      layout.map((other) => (other === item ? { ...item, ...change } : other)),
      cols,
    )
  const shifted = (x: number, y: number) => {
    const copy = layout.map((other) => ({ ...other }))
    const target = copy.find((other) => other.i === id)!
    const placed = moveElement(copy, target, x, y, true, false, 'vertical', cols)
    return verticalCompactor.compact(placed, cols)
  }

  switch (move) {
    case 'left':
      return item.x > 0 ? shifted(item.x - 1, item.y) : layout
    case 'right':
      return item.x + item.w < cols ? shifted(item.x + 1, item.y) : layout
    case 'up': {
      const above = sameColumns.filter((o) => o.y < item.y).sort((a, b) => b.y - a.y)[0]
      return above ? resized({ y: above.y - 1 }) : layout
    }
    case 'down': {
      const below = sameColumns.filter((o) => o.y > item.y).sort((a, b) => a.y - b.y)[0]
      return below ? resized({ y: below.y + below.h }) : layout
    }
    case 'wider':
      return item.x + item.w < cols ? resized({ w: item.w + 1 }) : layout
    case 'narrower':
      return item.w > (item.minW ?? 1) ? resized({ w: item.w - 1 }) : layout
    case 'taller':
      return resized({ h: item.h + 1 })
    case 'shorter':
      return item.h > (item.minH ?? 1) ? resized({ h: item.h - 1 }) : layout
  }
}

export function moveFrame(
  state: WorkspaceState,
  visible: FrameId[],
  breakpoint: Breakpoint,
  id: FrameId,
  move: Move,
): WorkspaceState {
  const shown = gridLayouts(state, visible)[breakpoint]
  return mergeGrid(state, { [breakpoint]: moved(shown, id, move, COLS[breakpoint]) })
}

function isFrameId(value: unknown): value is FrameId {
  return FRAME_IDS.includes(value as FrameId)
}

function isItem(value: unknown): value is LayoutItem {
  const item = value as LayoutItem | null
  return (
    isFrameId(item?.i) &&
    [item?.x, item?.y, item?.w, item?.h].every((n) => Number.isInteger(n) && (n as number) >= 0)
  )
}

function isSaved(saved: Partial<WorkspaceState> & { version?: number }): boolean {
  const { open, collapsed, layouts, version } = saved
  return (
    (version === STORAGE_VERSION || version === TABBED_CASES_VERSION - 1) &&
    Array.isArray(open) &&
    open.every(isFrameId) &&
    Array.isArray(collapsed) &&
    collapsed.every(isFrameId) &&
    BREAKPOINT_NAMES.every((b) => {
      const layout = layouts?.[b]
      return (
        Array.isArray(layout) &&
        layout.every(isItem) &&
        open.every((id) => layout.some((item) => item.i === id))
      )
    })
  )
}

/** The saved workspace, or the default when there is none or it cannot be used. */
export function loadWorkspace(userId: string, slug: string): WorkspaceState {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey(userId, slug)) ?? 'null')
    if (saved && isSaved(saved)) {
      const state: WorkspaceState = { open: saved.open, collapsed: saved.collapsed, layouts: saved.layouts }
      // Sections that used to open as frames are tabs of the case now.
      return saved.version < TABBED_CASES_VERSION ? CASE_FRAMES.reduce(closeFrame, state) : state
    }
  } catch {
    // Unreadable or blocked storage falls through to the default.
  }
  return defaultWorkspace()
}

export function saveWorkspace(userId: string, slug: string, state: WorkspaceState) {
  try {
    localStorage.setItem(
      storageKey(userId, slug),
      JSON.stringify({ version: STORAGE_VERSION, ...state }),
    )
  } catch {
    // Remembering the layout is a convenience only.
  }
}
