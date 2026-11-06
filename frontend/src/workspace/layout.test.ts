import type { Layout } from 'react-grid-layout'
import { afterEach, describe, expect, test, vi } from 'vitest'
import {
  closeCase,
  closeFrame,
  COLS,
  defaultWorkspace,
  gridLayouts,
  loadWorkspace,
  mergeGrid,
  moveFrame,
  openCase,
  openFrame,
  readingOrder,
  saveWorkspace,
  toggleCollapsed,
  type Breakpoint,
  type FrameId,
  type Move,
  type WorkspaceState,
} from './layout'

const BREAKPOINTS: Breakpoint[] = ['lg', 'md', 'sm', 'xs']

afterEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
})

function at(layout: Layout, id: FrameId) {
  return layout.find((item) => item.i === id)!
}

function overlapping(layout: Layout): boolean {
  return layout.some((a) =>
    layout.some(
      (b) =>
        a !== b && a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h,
    ),
  )
}

/** Two frames side by side on top, one under the left frame. */
function arranged(): WorkspaceState {
  const lg = [
    { i: 'queue', x: 0, y: 0, w: 4, h: 10 },
    { i: 'overview', x: 4, y: 0, w: 4, h: 10 },
    { i: 'search', x: 0, y: 10, w: 4, h: 10 },
  ]
  const defaults = openFrame(closeFrame(defaultWorkspace(), 'deadlines'), 'search')
  return { ...defaults, layouts: { ...defaults.layouts, lg } }
}

const VISIBLE: FrameId[] = ['queue', 'overview', 'search']

function move(state: WorkspaceState, id: FrameId, ...moves: Move[]) {
  return moves.reduce((current, m) => moveFrame(current, VISIBLE, 'lg', id, m), state)
}

describe('placing frames', () => {
  test.each(BREAKPOINTS)('the default frames do not overlap and fit %s', (breakpoint) => {
    const layout = defaultWorkspace().layouts[breakpoint]
    expect(layout.map((item) => item.i)).toEqual(['queue', 'overview', 'deadlines'])
    expect(overlapping(layout)).toBe(false)
    expect(layout.every((item) => item.x + item.w <= COLS[breakpoint])).toBe(true)
  })

  test('phones stack every frame in one column', () => {
    const state = openFrame(defaultWorkspace(), 'observable')
    expect(state.layouts.xs.every((item) => item.x === 0 && item.w === 1)).toBe(true)
    expect(overlapping(state.layouts.xs)).toBe(false)
  })

  test('a new frame takes the first free space', () => {
    const state = openFrame(defaultWorkspace(), 'observable')
    expect(at(state.layouts.lg, 'observable')).toMatchObject({ x: 0, y: 20 })
  })

  test('a case opens its core frames at the top, beside the queue, pushing others down', () => {
    const { layouts, open } = openCase(defaultWorkspace())
    expect(open).toEqual(expect.arrayContaining(['case', 'case-tasks', 'case-timeline']))
    expect(at(layouts.lg, 'case')).toMatchObject({ x: 5, y: 0, w: 7 })
    expect(at(layouts.lg, 'case-tasks').y).toBe(at(layouts.lg, 'case').h)
    expect(at(layouts.lg, 'queue')).toMatchObject({ x: 0, y: 0 })
    expect(at(layouts.lg, 'overview').y).toBeGreaterThan(at(layouts.lg, 'case-timeline').y)
    expect(overlapping(layouts.lg)).toBe(false)
  })

  test('a case keeps the arrangement of frames the user already chose', () => {
    const arrangedCase = closeFrame(openCase(defaultWorkspace()), 'case-tasks')
    expect(openCase(arrangedCase).open).not.toContain('case-tasks')
  })

  test('closing the case closes all its frames and leaves the others', () => {
    const state = closeCase(openFrame(openFrame(openCase(defaultWorkspace()), 'case-notes'), 'custody'))
    expect(state.open).toEqual(['queue', 'overview', 'deadlines'])
  })

  test('opening an open frame changes nothing, unless it needs expanding', () => {
    const state = defaultWorkspace()
    expect(openFrame(state, 'queue')).toBe(state)
    const collapsed = toggleCollapsed(state, 'queue')
    expect(openFrame(collapsed, 'queue').collapsed).toEqual([])
  })

  test('closing a frame removes it everywhere', () => {
    const state = closeFrame(toggleCollapsed(defaultWorkspace(), 'deadlines'), 'deadlines')
    expect(state.open).toEqual(['queue', 'overview'])
    expect(state.collapsed).toEqual([])
    expect(BREAKPOINTS.every((b) => !state.layouts[b].some((item) => item.i === 'deadlines'))).toBe(true)
  })
})

describe('the grid', () => {
  test('shows only visible frames, with their minimum sizes', () => {
    const shown = gridLayouts(defaultWorkspace(), ['queue']).lg
    expect(shown).toHaveLength(1)
    expect(shown[0]).toMatchObject({ i: 'queue', minW: 3, minH: 6 })
  })

  test('shrinks a collapsed frame to its title bar but remembers its height', () => {
    const state = toggleCollapsed(arranged(), 'queue')
    const shown = gridLayouts(state, VISIBLE)
    expect(at(shown.lg, 'queue')).toMatchObject({ h: 1, isResizable: false })
    const merged = mergeGrid(state, shown)
    expect(at(merged.layouts.lg, 'queue').h).toBe(10)
    expect(at(gridLayouts(toggleCollapsed(merged, 'queue'), VISIBLE).lg, 'queue').h).toBe(10)
  })

  test('keeps a hidden frame when the grid reports only what it shows', () => {
    const state = openCase(defaultWorkspace())
    const merged = mergeGrid(state, gridLayouts(state, VISIBLE))
    expect(at(merged.layouts.lg, 'case')).toEqual(at(state.layouts.lg, 'case'))
  })

  test('reading order runs across each row, then down', () => {
    const { lg } = arranged().layouts
    expect(readingOrder(lg)).toEqual(['queue', 'overview', 'search'])
    expect(readingOrder([...lg].reverse())).toEqual(['queue', 'overview', 'search'])
    expect(readingOrder(move(arranged(), 'search', 'up').layouts.lg)[0]).toBe('search')
  })

  test('returns the same state when nothing moved, so saving does not loop', () => {
    const state = defaultWorkspace()
    expect(mergeGrid(state, gridLayouts(state, VISIBLE))).toBe(state)
  })
})

describe('keyboard moves', () => {
  test('left and right move one column and stop at the edges', () => {
    const state = closeFrame(arranged(), 'search')
    const right = move(state, 'overview', 'right')
    expect(at(right.layouts.lg, 'overview').x).toBe(5)
    expect(move(state, 'queue', 'left')).toBe(state)
    const atEdge = { ...state, layouts: { ...state.layouts, lg: [{ i: 'queue', x: 8, y: 0, w: 4, h: 10 }] } }
    expect(move(atEdge, 'queue', 'right')).toBe(atEdge)
  })

  test('moving sideways into a neighbour keeps frames apart', () => {
    const { layouts } = move(closeFrame(arranged(), 'search'), 'overview', 'left')
    expect(at(layouts.lg, 'overview').x).toBe(3)
    expect(overlapping(layouts.lg)).toBe(false)
  })

  test('up and down swap with the frame in the way', () => {
    const up = move(arranged(), 'search', 'up').layouts.lg
    expect(at(up, 'search').y).toBe(0)
    expect(at(up, 'queue').y).toBe(10)
    const down = move(arranged(), 'queue', 'down').layouts.lg
    expect(at(down, 'search').y).toBe(0)
    expect(at(down, 'queue').y).toBe(10)
  })

  test('up and down do nothing at the ends of a column', () => {
    const state = arranged()
    expect(move(state, 'queue', 'up')).toBe(state)
    expect(move(state, 'search', 'down')).toBe(state)
  })

  test('resizing respects the grid and minimum sizes', () => {
    const state = arranged()
    expect(at(move(state, 'overview', 'wider').layouts.lg, 'overview').w).toBe(5)
    expect(at(move(state, 'overview', 'narrower').layouts.lg, 'overview').w).toBe(3)
    expect(at(move(state, 'overview', 'narrower', 'narrower').layouts.lg, 'overview').w).toBe(3)
    expect(at(move(state, 'queue', 'taller').layouts.lg, 'queue').h).toBe(11)
    expect(at(move(state, 'queue', 'taller').layouts.lg, 'search').y).toBe(11)
    expect(at(move(state, 'queue', 'shorter').layouts.lg, 'queue').h).toBe(9)
    const short = { ...state, layouts: { ...state.layouts, lg: [{ i: 'queue', x: 0, y: 0, w: 12, h: 6 }] } }
    expect(move(short, 'queue', 'shorter')).toBe(short)
    expect(move(short, 'queue', 'wider')).toBe(short)
  })

  test('a move at one breakpoint leaves the others alone', () => {
    const state = arranged()
    const moved = move(state, 'overview', 'wider')
    expect(moved.layouts.md).toEqual(state.layouts.md)
  })
})

describe('saved layouts', () => {
  const KEY = 'casemgr.layout.u1.dr'

  test('restores what was saved, per user and tenant', () => {
    const state = toggleCollapsed(move(arranged(), 'overview', 'wider'), 'search')
    saveWorkspace('u1', 'dr', state)
    expect(loadWorkspace('u1', 'dr')).toEqual(state)
    expect(loadWorkspace('u2', 'dr')).toEqual(defaultWorkspace())
    expect(loadWorkspace('u1', 'other')).toEqual(defaultWorkspace())
  })

  test('stores frame ids and positions only', () => {
    saveWorkspace('u1', 'dr', openFrame(defaultWorkspace(), 'case'))
    const saved = JSON.parse(localStorage.getItem(KEY)!)
    expect(Object.keys(saved).sort()).toEqual(['collapsed', 'layouts', 'open', 'version'])
    expect(Object.keys(saved.layouts.lg[0]).sort()).toEqual(['h', 'i', 'w', 'x', 'y'])
  })

  test.each([
    ['broken JSON', '{nope'],
    ['an empty value', 'null'],
    ['an older version', JSON.stringify({ ...defaultWorkspace(), version: 1 })],
    ['an unknown frame', JSON.stringify({ ...defaultWorkspace(), version: 2, open: ['gone'] })],
    ['a missing breakpoint', JSON.stringify({ ...defaultWorkspace(), version: 2, layouts: {} })],
    [
      'an open frame with no position',
      JSON.stringify({ ...closeFrame(defaultWorkspace(), 'deadlines'), version: 2, open: ['queue', 'deadlines'] }),
    ],
    [
      'a bad position',
      JSON.stringify({
        ...defaultWorkspace(),
        version: 2,
        layouts: { ...defaultWorkspace().layouts, lg: [{ i: 'queue', x: -1, y: 0, w: 1, h: 'tall' }] },
      }),
    ],
  ])('falls back to the default for %s', (_, saved) => {
    localStorage.setItem(KEY, saved)
    expect(loadWorkspace('u1', 'dr')).toEqual(defaultWorkspace())
  })

  test('works when storage is unavailable', () => {
    const blocked = () => {
      throw new Error('blocked')
    }
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(blocked)
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(blocked)
    expect(loadWorkspace('u1', 'dr')).toEqual(defaultWorkspace())
    expect(() => saveWorkspace('u1', 'dr', defaultWorkspace())).not.toThrow()
  })
})
