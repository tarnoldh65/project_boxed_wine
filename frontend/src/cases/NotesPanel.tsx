import { useQuery } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api, type Case, type Member, type Note } from '../api/client'
import { actorName, PRIVILEGE_NOTICE, useCaseAction } from './journal'

/** Notes are Markdown; raw HTML is never rendered and unsafe link targets are dropped. */
function Rendered({ body }: { body: string }) {
  return (
    <div className="markdown">
      <Markdown remarkPlugins={[remarkGfm]}>{body}</Markdown>
    </div>
  )
}

function History({ slug, number, note, members }: { slug: string; number: number; note: Note; members: Member[] }) {
  const revisions = useQuery({
    queryKey: ['revisions', note.id, note.revision],
    queryFn: async () => {
      const { data, response } = await api.GET(
        '/api/tenants/{slug}/cases/{number}/notes/{note_id}/revisions',
        { params: { path: { slug, number, note_id: note.id } } },
      )
      if (!data) throw new Error(`Could not load history (${response.status})`)
      return data
    },
  })
  if (revisions.isError) return <p role="alert">{revisions.error.message}</p>
  return (
    <ol className="history">
      {revisions.data?.map((r) => (
        <li key={r.revision}>
          Revision {r.revision} by {actorName(r.edited_by, members)},{' '}
          {new Date(r.edited_at).toLocaleString()}
          {r.privileged && ' (privileged)'}
          <pre>{r.body}</pre>
        </li>
      ))}
    </ol>
  )
}

function NoteForm({
  initial,
  submitLabel,
  onSubmit,
  onCancel,
}: {
  initial?: Note
  submitLabel: string
  onSubmit: (body: string, privileged: boolean) => void
  onCancel?: () => void
}) {
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    onSubmit(String(form.get('body')), form.get('privileged') === 'on')
    if (!initial) event.currentTarget.reset()
  }
  return (
    <form className="stack" onSubmit={submit}>
      <textarea
        name="body"
        rows={6}
        required
        aria-label={initial ? 'Edit note' : 'New note'}
        placeholder="Markdown; @username notifies a teammate"
        defaultValue={initial?.body}
      />
      <label>
        <input name="privileged" type="checkbox" defaultChecked={initial?.privileged} /> Privileged
      </label>
      <div className="inline-form">
        <button type="submit">{submitLabel}</button>
        {onCancel && (
          <button type="button" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
    </form>
  )
}

export function NotesPanel({
  slug,
  current,
  members,
  canWrite,
}: {
  slug: string
  current: Case
  members: Member[]
  canWrite: boolean
}) {
  const action = useCaseAction(slug, current.number)
  const [editing, setEditing] = useState<string | null>(null)
  const [history, setHistory] = useState<string | null>(null)
  const path = { params: { path: { slug, number: current.number } } }

  return (
    <section>
      <h3>Notes</h3>
      {current.notes.length === 0 && <p>No notes.</p>}
      {current.notes.map((note) => (
        <article key={note.id} className="note">
          {note.privileged && <p className="banner">{PRIVILEGE_NOTICE}</p>}
          <p className="note-meta">
            {actorName(note.created_by, members)}, {new Date(note.created_at).toLocaleString()}
            {note.revision > 1 &&
              ` (edited by ${actorName(note.updated_by, members)}, revision ${note.revision})`}{' '}
            {canWrite && editing !== note.id && (
              <button type="button" onClick={() => setEditing(note.id)}>
                Edit note
              </button>
            )}{' '}
            <button type="button" onClick={() => setHistory(history === note.id ? null : note.id)}>
              History
            </button>
          </p>
          {editing === note.id ? (
            <NoteForm
              initial={note}
              submitLabel="Save note"
              onCancel={() => setEditing(null)}
              onSubmit={(body, privileged) =>
                action.mutate(
                  () =>
                    api.PATCH('/api/tenants/{slug}/cases/{number}/notes/{note_id}', {
                      params: { path: { slug, number: current.number, note_id: note.id } },
                      body: { body, privileged },
                    }),
                  { onSuccess: () => setEditing(null) },
                )
              }
            />
          ) : (
            <Rendered body={note.body} />
          )}
          {history === note.id && <History slug={slug} number={current.number} note={note} members={members} />}
        </article>
      ))}
      {canWrite && (
        <NoteForm
          submitLabel="Add note"
          onSubmit={(body, privileged) =>
            action.mutate(() =>
              api.POST('/api/tenants/{slug}/cases/{number}/notes', { ...path, body: { body, privileged } }),
            )
          }
        />
      )}
      {action.error && <p role="alert">{action.error.message}</p>}
    </section>
  )
}
