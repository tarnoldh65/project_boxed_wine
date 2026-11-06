import type { FormEvent } from 'react'
import { api, type Case, type Member } from '../api/client'
import { actorName, useCaseAction } from './journal'

export function CommentsPanel({
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

  function post(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const body = String(new FormData(event.currentTarget).get('body'))
    event.currentTarget.reset()
    action.mutate(() =>
      api.POST('/api/tenants/{slug}/cases/{number}/comments', {
        params: { path: { slug, number: current.number } },
        body: { body },
      }),
    )
  }

  return (
    <section>
      {current.comments.length === 0 && <p className="empty">No comments.</p>}
      <ul className="comments">
        {current.comments.map((comment) => (
          <li key={comment.id}>
            <strong>{actorName(comment.author, members)}</strong>{' '}
            <span className="note-meta">{new Date(comment.created_at).toLocaleString()}</span>
            <p className="comment-body">{comment.body}</p>
          </li>
        ))}
      </ul>
      {canWrite && (
        <form className="inline-form" onSubmit={post}>
          <textarea name="body" rows={2} required maxLength={5000} aria-label="New comment" />
          <button type="submit">Post comment</button>
        </form>
      )}
      {action.error && <p role="alert">{action.error.message}</p>}
    </section>
  )
}
