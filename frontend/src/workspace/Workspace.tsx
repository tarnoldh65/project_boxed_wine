import { lazy, Suspense, useMemo, type ReactNode } from 'react'
import { getBreakpointFromWidth, GridLayout, useContainerWidth, type Layout } from 'react-grid-layout'
import {
  AlertsFrame,
  AssetsFrame,
  CaseScope,
  CustodyFrame,
  DetailsFrame,
  ReportsFrame,
  type CaseView,
} from '../cases/CaseFrames'
import { CasesPage } from '../cases/CasesPage'
import { CaseSummary } from '../cases/CaseSummary'
import { CommentsPanel } from '../cases/CommentsPanel'
import { EvidencePanel } from '../cases/EvidencePanel'
import { ObservablePage } from '../cases/ObservablePage'
import { ObservablesPanel } from '../cases/ObservablesPanel'
import { PirPanel } from '../cases/PirPanel'
import { RegulatoryPanel } from '../cases/RegulatoryPanel'
import { TasksPanel } from '../cases/TasksPanel'
import { TimelinePanel } from '../cases/TimelinePanel'
import { useCollector, useSplitSelection, type SplitSelection } from '../cases/useCase'
import { DeadlinesFrame } from '../insight/DeadlinesFrame'
import { MetricsFrame } from '../insight/MetricsFrame'
import { OverviewFrame } from '../insight/OverviewFrame'
import { SearchPage } from '../SearchPage'
import { Skeleton } from '../Skeleton'
import { Frame } from './Frame'
import {
  BREAKPOINTS,
  COLS,
  FRAMES,
  gridLayouts,
  readingOrder,
  type Breakpoint,
  type FrameId,
} from './layout'
import { useWorkspaceContext, type Selection, type Workspace as WorkspaceState } from './useWorkspace'

// Notes bring in the Markdown renderer, so it loads only when a Notes frame opens.
const NotesPanel = lazy(() =>
  import('../cases/NotesPanel').then((module) => ({ default: module.NotesPanel })),
)

const ROW_HEIGHT = 36
const GAP = 12

function title(id: FrameId, { caseNumber }: Selection): string {
  if (id === 'case') return `Case #${caseNumber}`
  return id.startsWith('case-') ? `${FRAMES[id].title} #${caseNumber}` : FRAMES[id].title
}

function caseContent(
  id: FrameId,
  view: CaseView,
  workspace: WorkspaceState,
  split: SplitSelection,
  collector: string,
): ReactNode {
  const { slug, current, members, permissions, canWrite, editable } = view
  const journal = { slug, current, members }
  switch (id) {
    case 'case':
      return (
        <CaseSummary
          view={view}
          split={split}
          tab={workspace.caseTab}
          popped={workspace.visible}
          onTab={workspace.setCaseTab}
          onPopOut={workspace.popOut}
          onReveal={workspace.show}
          renderPart={(part) => caseContent(part, view, workspace, split, collector)}
        />
      )
    case 'case-details':
      return <DetailsFrame view={view} />
    case 'case-tasks':
      return <TasksPanel {...journal} editable={editable} />
    case 'case-timeline':
      return <TimelinePanel {...journal} editable={canWrite} />
    case 'case-notes':
      return (
        <Suspense fallback={<Skeleton />}>
          <NotesPanel {...journal} canWrite={canWrite} />
        </Suspense>
      )
    case 'case-comments':
      return <CommentsPanel {...journal} canWrite={canWrite} />
    case 'case-observables':
      return (
        <ObservablesPanel
          slug={slug}
          current={current}
          editable={canWrite}
          selected={split.observables}
          onSelect={(ids) => split.choose('observables', ids)}
        />
      )
    case 'case-evidence':
      return (
        <EvidencePanel
          slug={slug}
          current={current}
          permissions={permissions}
          collector={collector}
          onCustody={workspace.showCustody}
        />
      )
    case 'case-assets':
      return <AssetsFrame view={view} split={split} />
    case 'case-alerts':
      return <AlertsFrame view={view} split={split} />
    case 'case-regulatory':
      return (
        <RegulatoryPanel
          {...journal}
          canWrite={canWrite}
          canDecide={permissions.includes('legal:manage')}
        />
      )
    case 'case-pir':
      return <PirPanel {...journal} canWrite={canWrite} />
    case 'case-reports':
      return <ReportsFrame view={view} />
    case 'custody':
      return <CustodyFrame view={view} evidenceId={workspace.selection.evidenceId!} />
  }
}

function content(
  id: FrameId,
  workspace: WorkspaceState,
  split: SplitSelection,
  collector: string,
): ReactNode {
  const { selection, queueFilter } = workspace
  switch (id) {
    case 'queue':
      return (
        <CasesPage
          filter={queueFilter}
          onFilter={workspace.setQueueFilter}
          activeCase={selection.caseNumber}
        />
      )
    case 'overview':
      return (
        <OverviewFrame
          filter={queueFilter}
          onFilter={workspace.filterQueue}
          onMetrics={() => workspace.show('metrics')}
        />
      )
    case 'deadlines':
      return <DeadlinesFrame />
    case 'metrics':
      return <MetricsFrame />
    case 'search':
      return <SearchPage q={selection.query ?? ''} />
    case 'observable':
      return (
        <ObservablePage key={selection.observableId} observableId={selection.observableId!} />
      )
    default:
      return (
        <CaseScope key={selection.caseNumber} number={selection.caseNumber!}>
          {(view) => caseContent(id, view, workspace, split, collector)}
        </CaseScope>
      )
  }
}

export function Workspace() {
  const workspace = useWorkspaceContext()
  const { state, selection, visible, maximized } = workspace
  const split = useSplitSelection(selection.caseNumber)
  const collector = useCollector()
  const { width, containerRef, mounted } = useContainerWidth({ measureBeforeMount: true })
  const breakpoint = getBreakpointFromWidth(BREAKPOINTS, width) as Breakpoint
  const stacked = breakpoint === 'xs'
  const layouts = useMemo(() => gridLayouts(state, visible), [state, visible])
  const reading = useMemo(() => readingOrder(layouts[breakpoint]), [layouts, breakpoint])
  const gridConfig = useMemo(
    () => ({
      cols: COLS[breakpoint],
      rowHeight: ROW_HEIGHT,
      margin: [GAP, GAP] as const,
      containerPadding: [0, 0] as const,
    }),
    [breakpoint],
  )
  const dragConfig = useMemo(() => ({ enabled: !stacked, handle: '.frame-handle' }), [stacked])
  const resizeConfig = useMemo(
    () => ({ enabled: !stacked, handles: ['s', 'e', 'se'] as const }),
    [stacked],
  )
  const saveMove = (layout: Layout) => workspace.gridChanged({ [breakpoint]: layout })

  return (
    <div ref={containerRef} className={stacked ? 'workspace stacked' : 'workspace'}>
      {visible.length === 0 && <p className="empty">No frames are open. Add one from Frames above.</p>}
      {mounted && (
        <GridLayout
          width={width}
          layout={layouts[breakpoint]}
          gridConfig={gridConfig}
          dragConfig={dragConfig}
          resizeConfig={resizeConfig}
          onDragStop={saveMove}
          onResizeStop={saveMove}
        >
          {reading.map((id) => (
            <div key={id} className={id === maximized ? 'frame-maximized' : undefined}>
              <Frame
                id={id}
                title={title(id, selection)}
                collapsed={state.collapsed.includes(id)}
                maximized={id === maximized}
                stacked={stacked}
                onCollapse={() => workspace.toggleCollapsed(id)}
                onMaximize={() => workspace.toggleMaximized(id)}
                onClose={() => workspace.close(id)}
                onMove={(move) => workspace.move(breakpoint, id, move)}
                onPutBack={id.startsWith('case-') ? () => workspace.putBack(id) : undefined}
              >
                {content(id, workspace, split, collector)}
              </Frame>
            </div>
          ))}
        </GridLayout>
      )}
    </div>
  )
}
