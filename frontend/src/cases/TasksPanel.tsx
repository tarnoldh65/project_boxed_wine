import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import {
  api,
  describeError,
  type Case,
  type Member,
  type Task,
  type TaskStatus,
  type TaskUpdate,
} from '../api/client'

const STATUSES: TaskStatus[] = ['open', 'in_progress', 'done']

export function TasksPanel({
  slug,
  current,
  members,
  editable,
}: {
  slug: string
  current: Case
  members: Member[]
  editable: boolean
}) {
  const queryClient = useQueryClient()
  const [waiving, setWaiving] = useState<string | null>(null)
  const number = current.number
  const titles = Object.fromEntries(current.tasks.map((t) => [t.id, t.title]))

  const save = useMutation({
    mutationFn: async (
      call: () => Promise<{ data?: Case; error?: unknown; response: Response }>,
    ) => {
      const { data, error, response } = await call()
      if (!data) throw new Error(describeError(error, response.status))
      return data
    },
    onSuccess: (updated) => {
      setWaiving(null)
      queryClient.setQueryData(['case', slug, number], updated)
    },
  })

  const update = (task: Task, body: TaskUpdate) =>
    save.mutate(() =>
      api.PATCH('/api/tenants/{slug}/cases/{number}/tasks/{task_id}', {
        params: { path: { slug, number, task_id: task.id } },
        body,
      }),
    )

  function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const due = String(form.get('due'))
    event.currentTarget.reset()
    save.mutate(() =>
      api.POST('/api/tenants/{slug}/cases/{number}/tasks', {
        params: { path: { slug, number } },
        body: {
          title: String(form.get('title')),
          description: '',
          checklist: [],
          assignee_id: String(form.get('assignee')) || null,
          due_at: due ? new Date(due).toISOString() : null,
          depends_on: form.getAll('depends_on').map(String),
        },
      }),
    )
  }

  function waive(task: Task, event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const reason = String(new FormData(event.currentTarget).get('reason'))
    update(task, { status: 'waived', waive_reason: reason })
  }

  return (
    <section>
      <h3>Tasks</h3>
      {current.tasks.length === 0 && <p>No tasks.</p>}
      {current.tasks.map((task) => (
        <div key={task.id} className="task">
          <div className="task-head">
            <strong>{task.title}</strong>
            {task.blocked && <span className="badge">blocked</span>}
            {task.status === 'waived' ? (
              <span>
                Waived: {task.waive_reason}{' '}
                {editable && (
                  <button type="button" onClick={() => update(task, { status: 'open' })}>
                    Reopen {task.title}
                  </button>
                )}
              </span>
            ) : (
              <select
                aria-label={`Status of ${task.title}`}
                value={task.status}
                disabled={!editable}
                onChange={(e) => update(task, { status: e.target.value as TaskStatus })}
              >
                {STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {s.replace('_', ' ')}
                  </option>
                ))}
              </select>
            )}
            <select
              aria-label={`Assignee of ${task.title}`}
              value={task.assignee_id ?? ''}
              disabled={!editable}
              onChange={(e) => update(task, { assignee_id: e.target.value || null })}
            >
              <option value="">Unassigned</option>
              {members.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.display_name}
                </option>
              ))}
            </select>
            {task.due_at && <span>due {new Date(task.due_at).toLocaleString()}</span>}
            {editable && task.status !== 'waived' && waiving !== task.id && (
              <button type="button" onClick={() => setWaiving(task.id)}>
                Waive {task.title}
              </button>
            )}
          </div>
          {task.depends_on.length > 0 && (
            <p>After: {task.depends_on.map((id) => titles[id] ?? 'removed task').join(', ')}</p>
          )}
          {task.checklist.length > 0 && (
            <ul className="checklist">
              {task.checklist.map((item, i) => (
                <li key={i}>
                  <label>
                    <input
                      type="checkbox"
                      checked={item.done ?? false}
                      disabled={!editable}
                      onChange={(e) =>
                        update(task, {
                          checklist: task.checklist.map((it, j) =>
                            j === i ? { ...it, done: e.target.checked } : it,
                          ),
                        })
                      }
                    />{' '}
                    {item.text}
                  </label>
                </li>
              ))}
            </ul>
          )}
          {waiving === task.id && (
            <form className="inline-form" onSubmit={(e) => waive(task, e)}>
              <input name="reason" required aria-label={`Reason to waive ${task.title}`} placeholder="Reason" />
              <button type="submit">Confirm waive</button>
              <button type="button" onClick={() => setWaiving(null)}>
                Cancel
              </button>
            </form>
          )}
        </div>
      ))}
      {editable && (
        <form className="inline-form" onSubmit={add}>
          <input name="title" required maxLength={300} aria-label="New task title" placeholder="New task" />
          <select name="assignee" aria-label="New task assignee">
            <option value="">Unassigned</option>
            {members.map((m) => (
              <option key={m.id} value={m.id}>
                {m.display_name}
              </option>
            ))}
          </select>
          <input name="due" type="datetime-local" aria-label="New task due" />
          {current.tasks.length > 0 && (
            <select name="depends_on" multiple aria-label="New task depends on">
              {current.tasks.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.title}
                </option>
              ))}
            </select>
          )}
          <button type="submit">Add task</button>
        </form>
      )}
      {save.error && <p role="alert">{save.error.message}</p>}
    </section>
  )
}
