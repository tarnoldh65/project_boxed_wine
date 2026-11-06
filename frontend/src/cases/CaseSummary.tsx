import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router'
import { api, describeError, type Case, type Resolution } from '../api/client'
import { Badge, SeverityBadge, SlaBadge, TlpBadge } from '../Badge'
import { Dialog } from '../Dialog'
import { CASE_FRAMES, FRAMES, type FrameId } from '../workspace/layout'
import type { CaseView } from './CaseFrames'
import type { SplitSelection } from './useCase'

const RESOLUTIONS: Resolution[] = ['true_positive', 'false_positive', 'benign', 'duplicate']

type CaseCall = () => Promise<{ data?: Case; error?: unknown; response: Response }>

/** How many items a case frame's chip counts. */
function itemCount(id: FrameId, current: Case): number | null {
  const counts: Partial<Record<FrameId, unknown[]>> = {
    'case-tasks': current.tasks,
    'case-timeline': current.timeline,
    'case-notes': current.notes,
    'case-comments': current.comments,
    'case-observables': current.observables,
    'case-evidence': current.evidence,
    'case-assets': current.assets,
    'case-alerts': current.alerts,
    'case-regulatory': current.clocks,
  }
  return counts[id]?.length ?? null
}

function ActionDialog({
  label,
  disabled,
  error,
  onOpen,
  onSubmit,
  children,
}: {
  label: string
  disabled?: boolean
  error: Error | null
  onOpen: () => void
  /** Runs the action from the form's fields; `done` closes the dialog. */
  onSubmit: (form: FormData, done: () => void) => void
  children: ReactNode
}) {
  const [open, setOpen] = useState(false)

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    onSubmit(new FormData(event.currentTarget), () => setOpen(false))
  }

  return (
    <>
      <button
        type="button"
        disabled={disabled}
        onClick={() => {
          onOpen()
          setOpen(true)
        }}
      >
        {label}
      </button>
      {open && (
        <Dialog title={label} onClose={() => setOpen(false)}>
          <form className="stack" onSubmit={submit}>
            {children}
            {error && <p role="alert">{error.message}</p>}
            <div className="inline-form">
              <button type="submit">Confirm</button>
              <button type="button" onClick={() => setOpen(false)}>
                Cancel
              </button>
            </div>
          </form>
        </Dialog>
      )}
    </>
  )
}

function ReasonField({ label }: { label: string }) {
  return (
    <label>
      {label}
      <input name="reason" required maxLength={1000} autoFocus />
    </label>
  )
}

function StateFields({ view }: { view: CaseView }) {
  const { config, current } = view
  const [state, setState] = useState(current.state)
  const terminal = config.states.some((s) => s.key === state && s.terminal)
  return (
    <>
      <label>
        State
        <select name="state" value={state} onChange={(e) => setState(e.target.value)}>
          {config.states.map((s) => (
            <option key={s.key} value={s.key}>
              {s.name}
            </option>
          ))}
        </select>
      </label>
      {terminal && (
        <label>
          Resolution
          <select name="resolution" defaultValue={current.resolution ?? ''}>
            <option value="" />
            {RESOLUTIONS.map((r) => (
              <option key={r} value={r}>
                {r.replace('_', ' ')}
              </option>
            ))}
          </select>
        </label>
      )}
    </>
  )
}

export function CaseSummary({
  view,
  split,
  openFrames,
  onOpenFrame,
}: {
  view: CaseView
  split: SplitSelection
  openFrames: FrameId[]
  onOpenFrame: (id: FrameId) => void
}) {
  const { slug, current, config, members, permissions, editable } = view
  const number = current.number
  const path = { params: { path: { slug, number } } }
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const state = config.states.find((s) => s.key === current.state)
  const owner = members.find((m) => m.id === current.owner_id)?.display_name ?? 'Unassigned'
  const legal = permissions.includes('legal:manage')

  // Every case action returns the case to show next.
  const caseAction = {
    mutationFn: async (run: CaseCall) => {
      const { data, error, response } = await run()
      if (!data) throw new Error(describeError(error, response.status))
      return data
    },
    onSuccess: (shown: Case) => {
      queryClient.setQueryData(['case', slug, shown.number], shown)
      if (shown.number !== number) {
        split.clear()
        navigate(`/t/${slug}/cases/${shown.number}`)
      }
    },
  }
  const action = useMutation(caseAction)
  const acknowledge = useMutation(caseAction)

  const run = (call: CaseCall, done: () => void) => action.mutate(call, { onSuccess: done })

  function changeState(form: FormData, done: () => void) {
    const resolution = (form.get('resolution') as Resolution | null) || null
    run(
      () =>
        api.PATCH('/api/tenants/{slug}/cases/{number}', {
          ...path,
          body: {
            state: String(form.get('state')),
            ...(resolution !== current.resolution && { resolution }),
          },
        }),
      done,
    )
  }

  const reason = (form: FormData) => String(form.get('reason'))
  const selected = split.alerts.length + split.assets.length + split.observables.length

  return (
    <article>
      <h2>
        #{number} {current.title}
      </h2>
      <p className="case-meta">
        <SeverityBadge priority={current.priority} severity={current.severity} />
        <Badge>{state?.name ?? current.state}</Badge>
        <TlpBadge tlp={current.tlp} />
        {owner} | source {current.source} | opened {new Date(current.created_at).toLocaleString()}
        {current.closed_at && ` | closed ${new Date(current.closed_at).toLocaleString()}`}
      </p>
      {current.merged_into && (
        <p>
          Merged into <Link to={`/t/${slug}/cases/${current.merged_into}`}>#{current.merged_into}</Link>
        </p>
      )}
      {current.legal_hold && (
        <p className="banner">Legal hold: evidence on this case cannot be deleted.</p>
      )}
      {current.major_incident_at && (
        <p className="banner">
          Major incident declared {new Date(current.major_incident_at).toLocaleString()}:{' '}
          {current.major_incident_reason}
        </p>
      )}

      <table className="sla">
        <caption>SLA</caption>
        <tbody>
          <tr>
            {current.sla.map((clock) => (
              <td key={clock.step}>
                <strong>{clock.step}</strong> <SlaBadge status={clock.status} />
                <br />
                {clock.met_at
                  ? `met ${new Date(clock.met_at).toLocaleString()}`
                  : `due ${new Date(clock.due_at).toLocaleString()}`}
              </td>
            ))}
          </tr>
        </tbody>
      </table>

      <div className="inline-form">
        {editable && !current.acknowledged_at && (
          <button
            type="button"
            onClick={() =>
              acknowledge.mutate(() => api.POST('/api/tenants/{slug}/cases/{number}/acknowledge', path))
            }
          >
            Acknowledge
          </button>
        )}
        {view.canWrite && (
          <ActionDialog label="Change state" error={action.error} onOpen={action.reset} onSubmit={changeState}>
            <StateFields view={view} />
          </ActionDialog>
        )}
        {editable && !current.major_incident_at && (
          <ActionDialog
            label="Declare major incident"
            error={action.error}
            onOpen={action.reset}
            onSubmit={(form, done) =>
              run(
                () =>
                  api.POST('/api/tenants/{slug}/cases/{number}/major-incident', {
                    ...path,
                    body: { reason: reason(form) },
                  }),
                done,
              )
            }
          >
            <ReasonField label="Major incident reason" />
          </ActionDialog>
        )}
        {legal && (
          <ActionDialog
            label={current.privileged ? 'Remove privilege marking' : 'Mark privileged'}
            error={action.error}
            onOpen={action.reset}
            onSubmit={(form, done) =>
              run(
                () =>
                  api.PUT('/api/tenants/{slug}/cases/{number}/privilege', {
                    ...path,
                    body: { on: !current.privileged, reason: reason(form) },
                  }),
                done,
              )
            }
          >
            <ReasonField label="Privilege reason" />
          </ActionDialog>
        )}
        {legal && (
          <ActionDialog
            label={current.legal_hold ? 'Lift legal hold' : 'Place legal hold'}
            error={action.error}
            onOpen={action.reset}
            onSubmit={(form, done) =>
              run(
                () =>
                  api.PUT('/api/tenants/{slug}/cases/{number}/legal-hold', {
                    ...path,
                    body: { on: !current.legal_hold, reason: reason(form) },
                  }),
                done,
              )
            }
          >
            <ReasonField label="Legal hold reason" />
          </ActionDialog>
        )}
        {editable && (
          <ActionDialog
            label="Merge into case"
            error={action.error}
            onOpen={action.reset}
            onSubmit={(form, done) =>
              run(
                () =>
                  api.POST('/api/tenants/{slug}/cases/{number}/merge', {
                    ...path,
                    body: { into: Number(form.get('into')) },
                  }),
                done,
              )
            }
          >
            <label>
              Case number
              <input name="into" type="number" min={1} required autoFocus />
            </label>
          </ActionDialog>
        )}
        {editable && (
          <ActionDialog
            label="Split selected into new case"
            disabled={selected === 0}
            error={action.error}
            onOpen={action.reset}
            onSubmit={(form, done) =>
              run(
                () =>
                  api.POST('/api/tenants/{slug}/cases/{number}/split', {
                    ...path,
                    body: {
                      title: String(form.get('title')),
                      alert_ids: split.alerts,
                      asset_ids: split.assets,
                      observable_ids: split.observables,
                    },
                  }),
                done,
              )
            }
          >
            <label>
              New case title
              <input name="title" required maxLength={300} autoFocus />
            </label>
          </ActionDialog>
        )}
      </div>
      {acknowledge.error && <p role="alert">{acknowledge.error.message}</p>}

      <nav className="chips" aria-label="Case frames">
        {CASE_FRAMES.map((id) => {
          const count = itemCount(id, current)
          return (
            <button
              key={id}
              type="button"
              className="chip"
              aria-pressed={openFrames.includes(id)}
              onClick={() => onOpenFrame(id)}
            >
              {FRAMES[id].title}
              {count !== null && <span className="chip-count">{count}</span>}
            </button>
          )
        })}
      </nav>
    </article>
  )
}
