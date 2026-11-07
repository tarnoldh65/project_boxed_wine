import {
  ChevronDown,
  ChevronUp,
  EllipsisVertical,
  GripVertical,
  Maximize2,
  Minimize2,
  SquareArrowDownLeft,
  X,
} from 'lucide-react'
import type { ReactNode } from 'react'
import { Menu } from '../Menu'
import type { FrameId, Move } from './layout'

const POSITION_MOVES: [Move, string][] = [
  ['left', 'Move left'],
  ['right', 'Move right'],
  ['up', 'Move up'],
  ['down', 'Move down'],
]
const SIZE_MOVES: [Move, string][] = [
  ['wider', 'Wider'],
  ['narrower', 'Narrower'],
  ['taller', 'Taller'],
  ['shorter', 'Shorter'],
]

/** Moves that make sense for a frame: a stacked grid has no columns, a collapsed frame no height. */
function moves(stacked: boolean, collapsed: boolean): [Move, string][] {
  const position = stacked ? POSITION_MOVES.slice(2) : POSITION_MOVES
  const size = SIZE_MOVES.slice(stacked ? 2 : 0, collapsed ? 2 : 4)
  return [...position, ...size]
}

export function Frame({
  id,
  title,
  collapsed,
  maximized,
  stacked,
  onCollapse,
  onMaximize,
  onClose,
  onMove,
  onPutBack,
  children,
}: {
  id: FrameId
  title: string
  collapsed: boolean
  maximized: boolean
  stacked: boolean
  onCollapse: () => void
  onMaximize: () => void
  onClose: () => void
  onMove: (move: Move) => void
  /** For a popped-out case section: return it to the case as a tab. */
  onPutBack?: () => void
  children: ReactNode
}) {
  const titleId = `frame-title-${id}`
  return (
    <section id={`frame-${id}`} className="frame" aria-labelledby={titleId}>
      <header className="frame-header">
        <div className="frame-handle">
          {!stacked && <GripVertical aria-hidden size={14} />}
          <h2 id={titleId}>{title}</h2>
        </div>
        <div className="frame-actions">
          <Menu label={`Arrange ${title}`} trigger={<EllipsisVertical aria-hidden size={16} />}>
            {!maximized &&
              moves(stacked, collapsed).map(([move, label]) => (
                <button
                  key={move}
                  type="button"
                  className="ghost"
                  onClick={(event) => {
                    // Moving can reorder the frames in the page, which drops focus.
                    const button = event.currentTarget
                    onMove(move)
                    requestAnimationFrame(() => button.focus())
                  }}
                >
                  {label}
                </button>
              ))}
          </Menu>
          {onPutBack && (
            <button
              type="button"
              className="ghost"
              title="Put back in the case"
              aria-label={`Put ${title} back in the case`}
              onClick={onPutBack}
            >
              <SquareArrowDownLeft aria-hidden size={16} />
            </button>
          )}
          <button
            type="button"
            className="ghost"
            aria-label={`${collapsed ? 'Expand' : 'Collapse'} ${title}`}
            onClick={onCollapse}
          >
            {collapsed ? <ChevronDown aria-hidden size={16} /> : <ChevronUp aria-hidden size={16} />}
          </button>
          <button
            type="button"
            className="ghost"
            aria-label={`${maximized ? 'Restore' : 'Maximize'} ${title}`}
            onClick={onMaximize}
          >
            {maximized ? <Minimize2 aria-hidden size={16} /> : <Maximize2 aria-hidden size={16} />}
          </button>
          <button type="button" className="ghost" aria-label={`Close ${title}`} onClick={onClose}>
            <X aria-hidden size={16} />
          </button>
        </div>
      </header>
      {/* Hidden rather than removed, so unsaved work survives collapsing. */}
      <div className="frame-body" hidden={collapsed && !maximized}>
        {children}
      </div>
    </section>
  )
}
