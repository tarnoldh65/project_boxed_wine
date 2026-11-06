import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import { matchPath, useLocation, useNavigate } from 'react-router'
import { NO_FILTER, openCasesFilter, sameFilter, type QueueFilter } from '../cases/queue'
import {
  closeCase,
  closeFrame,
  defaultWorkspace,
  loadWorkspace,
  mergeGrid,
  moveFrame,
  openCase,
  openFrame,
  saveWorkspace,
  toggleCollapsed,
  type Breakpoint,
  type FrameId,
  type Move,
  type WorkspaceState,
} from './layout'

/** What the frames show. Selections live here, so a frame stays open when the address moves on. */
export interface Selection {
  caseNumber?: number
  evidenceId?: string
  observableId?: string
  query?: string
}

const REVEAL_DELAY_MS = 250

interface Target {
  frame: FrameId
  selection: Selection
}

/** The frame an address points at, if any. Every link in the app works by changing the address. */
function targetOf(pathname: string, search: string, root: string): Target | null {
  const match = (pattern: string) => matchPath(`${root}/${pattern}`, pathname)
  const caseNumber = Number(match('cases/:number')?.params.number)
  if (caseNumber) return { frame: 'case', selection: { caseNumber } }
  const observableId = match('observables/:observableId')?.params.observableId
  if (observableId) return { frame: 'observable', selection: { observableId } }
  if (match('dashboard')) return { frame: 'overview', selection: {} }
  if (match('search')) {
    return { frame: 'search', selection: { query: new URLSearchParams(search).get('q') ?? '' } }
  }
  return null
}

function follow(state: WorkspaceState, target: Target): WorkspaceState {
  return target.frame === 'case' ? openCase(state) : openFrame(state, target.frame)
}

function hasContent(id: FrameId, selection: Selection): boolean {
  if (id === 'custody') return selection.evidenceId !== undefined
  if (id.startsWith('case')) return selection.caseNumber !== undefined
  if (id === 'observable') return selection.observableId !== undefined
  return true
}

export function useWorkspace(userId: string, slug: string) {
  const { pathname, search } = useLocation()
  const navigate = useNavigate()
  const root = `/t/${slug}`
  const target = useMemo(() => targetOf(pathname, search, root), [pathname, search, root])
  const [state, setState] = useState(() => {
    const saved = loadWorkspace(userId, slug)
    return target ? follow(saved, target) : saved
  })
  const [selection, setSelection] = useState<Selection>(target?.selection ?? {})
  const [maximized, setMaximized] = useState<FrameId | null>(null)
  const [queueFilter, setQueueFilter] = useState(NO_FILTER)
  const [reveal, setReveal] = useState<{ id: FrameId } | null>(target && { id: target.frame })
  const visible = useMemo(
    () => state.open.filter((id) => hasContent(id, selection)),
    [state.open, selection],
  )

  useEffect(() => saveWorkspace(userId, slug, state), [userId, slug, state])

  // An effect, not an update during render: React can drop those while the grid's own
  // layout update is pending, and the selected frame would never open.
  useEffect(() => {
    if (!target) return
    // oxlint-disable-next-line react/set-state-in-effect -- following the address, an outside source
    setSelection((current) => {
      // Evidence belongs to its case, so a different case closes its custody view.
      const sameCase = !target.selection.caseNumber || target.selection.caseNumber === current.caseNumber
      return { ...current, ...target.selection, evidenceId: sameCase ? current.evidenceId : undefined }
    })
    setState((current) => follow(current, target))
  }, [target])

  // A frame can open below the fold. The grid animates it into place, so scroll once it lands.
  useEffect(() => {
    if (!reveal) return
    const timer = setTimeout(
      () => document.getElementById(`frame-${reveal.id}`)?.scrollIntoView?.({ block: 'nearest' }),
      REVEAL_DELAY_MS,
    )
    return () => clearTimeout(timer)
  }, [reveal])

  function close(id: FrameId) {
    setState((current) => (id === 'case' ? closeCase(current) : closeFrame(current, id)))
    if (id === 'case') {
      setSelection((current) => ({ ...current, caseNumber: undefined, evidenceId: undefined }))
    }
    if (id === 'custody') setSelection((current) => ({ ...current, evidenceId: undefined }))
    if (id === 'observable') setSelection((current) => ({ ...current, observableId: undefined }))
    if (maximized === id) setMaximized(null)
    // Otherwise the same link could not open this frame again.
    if (target?.frame === id) navigate(root, { replace: true })
  }

  return {
    state,
    selection,
    visible,
    maximized,
    target,
    close,
    toggleOpen(id: FrameId) {
      if (state.open.includes(id)) close(id)
      else setState((current) => openFrame(current, id))
      if (pathname === `${root}/settings`) navigate(root)
    },
    queueFilter,
    setQueueFilter,
    /** Show the queue filtered on a dashboard figure; selecting the same one again clears it. */
    filterQueue(part: Partial<QueueFilter>) {
      const next = openCasesFilter(part)
      setQueueFilter(sameFilter(queueFilter, next) ? NO_FILTER : next)
      setState((current) => openFrame(current, 'queue'))
    },
    /** Open a frame, or bring it into view if it is open already. */
    show(id: FrameId) {
      setState((current) => openFrame(current, id))
      setReveal({ id })
    },
    showCustody(evidenceId: string) {
      setSelection((current) => ({ ...current, evidenceId }))
      setState((current) => openFrame(current, 'custody'))
      setReveal({ id: 'custody' })
    },
    toggleCollapsed: (id: FrameId) => setState((current) => toggleCollapsed(current, id)),
    toggleMaximized: (id: FrameId) => setMaximized((current) => (current === id ? null : id)),
    move(breakpoint: Breakpoint, id: FrameId, move: Move) {
      setState((current) => moveFrame(current, visible, breakpoint, id, move))
    },
    gridChanged(grid: Parameters<typeof mergeGrid>[1]) {
      setState((current) => mergeGrid(current, grid))
    },
    reset() {
      setMaximized(null)
      setState(
        selection.caseNumber === undefined ? defaultWorkspace() : openCase(defaultWorkspace()),
      )
    },
  }
}

export type Workspace = ReturnType<typeof useWorkspace>

export const WorkspaceContext = createContext<Workspace | null>(null)

export function useWorkspaceContext(): Workspace {
  return useContext(WorkspaceContext)!
}
