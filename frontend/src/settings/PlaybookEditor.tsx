import { useState } from 'react'
import { api, type PlaybookTask, type Template, type TenantConfig } from '../api/client'
import { SaveBar } from './SaveBar'
import { removeAt, splitList, updateAt, useSaveSection } from './editing'

const CORE_FIELDS = ['severity', 'impact', 'urgency', 'tlp', 'pap', 'team']

interface Row {
  key: string
  title: string
  dueHours: string
  dependsOn: string
  field: string
  value: string
}

function toRow(task: PlaybookTask): Row {
  return {
    key: task.key,
    title: task.title,
    dueHours: task.due_hours ? String(task.due_hours) : '',
    dependsOn: task.depends_on.join(', '),
    field: task.when?.field ?? '',
    value: task.when?.value ?? '',
  }
}

function toTask(row: Row, previous: PlaybookTask | undefined): PlaybookTask {
  return {
    key: row.key,
    title: row.title,
    description: previous?.description ?? '',
    due_hours: row.dueHours ? Number(row.dueHours) : null,
    depends_on: splitList(row.dependsOn),
    when: row.field ? { field: row.field, value: row.value } : null,
  }
}

function TemplatePlaybook({ slug, config, template }: { slug: string; config: TenantConfig; template: Template }) {
  const [draft, setDraft] = useState(() => template.tasks.map(toRow))
  const save = useSaveSection(slug, (body: Template[]) =>
    api.PUT('/api/tenants/{slug}/config/templates', { params: { path: { slug } }, body }),
  )
  const fields = [...CORE_FIELDS, ...config.custom_fields.map((f) => f.key)]

  function move(index: number, offset: number) {
    const next = [...draft]
    ;[next[index], next[index + offset]] = [next[index + offset], next[index]]
    setDraft(next)
  }

  function submit() {
    const previous = Object.fromEntries(template.tasks.map((t) => [t.key, t]))
    const tasks = draft.map((row) => toTask(row, previous[row.key]))
    // Other template settings come from the latest saved configuration.
    save.mutate(config.templates.map((t) => (t.key === template.key ? { ...t, tasks } : t)))
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        submit()
      }}
    >
      <p>Tasks run in order; a task with a condition is added once the case matches it.</p>
      <table>
        <thead>
          <tr>
            <th>Key</th>
            <th>Title</th>
            <th>Due (hours)</th>
            <th>Depends on</th>
            <th>Only when</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {draft.map((row, i) => (
            <tr key={i}>
              <td>
                <input
                  aria-label={`Task ${i + 1} key`}
                  value={row.key}
                  onChange={(e) => setDraft(updateAt(draft, i, { key: e.target.value }))}
                />
              </td>
              <td>
                <input
                  aria-label={`Task ${i + 1} title`}
                  value={row.title}
                  onChange={(e) => setDraft(updateAt(draft, i, { title: e.target.value }))}
                />
              </td>
              <td>
                <input
                  type="number"
                  min={1}
                  aria-label={`Task ${i + 1} due hours`}
                  value={row.dueHours}
                  onChange={(e) => setDraft(updateAt(draft, i, { dueHours: e.target.value }))}
                />
              </td>
              <td>
                <input
                  aria-label={`Task ${i + 1} depends on`}
                  placeholder="earlier task keys"
                  value={row.dependsOn}
                  onChange={(e) => setDraft(updateAt(draft, i, { dependsOn: e.target.value }))}
                />
              </td>
              <td>
                <select
                  aria-label={`Task ${i + 1} condition field`}
                  value={row.field}
                  onChange={(e) => setDraft(updateAt(draft, i, { field: e.target.value }))}
                >
                  <option value="">Always</option>
                  {fields.map((f) => (
                    <option key={f}>{f}</option>
                  ))}
                </select>
                {row.field && (
                  <>
                    {' = '}
                    <input
                      aria-label={`Task ${i + 1} condition value`}
                      value={row.value}
                      onChange={(e) => setDraft(updateAt(draft, i, { value: e.target.value }))}
                    />
                  </>
                )}
              </td>
              <td>
                <button type="button" disabled={i === 0} onClick={() => move(i, -1)}>
                  Up
                </button>
                <button type="button" disabled={i === draft.length - 1} onClick={() => move(i, 1)}>
                  Down
                </button>
                <button type="button" onClick={() => setDraft(removeAt(draft, i))}>
                  Remove
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <button
        type="button"
        onClick={() =>
          setDraft([...draft, { key: '', title: '', dueHours: '', dependsOn: '', field: '', value: '' }])
        }
      >
        Add task
      </button>
      <SaveBar save={save} />
    </form>
  )
}

export function PlaybookEditor({ slug, config }: { slug: string; config: TenantConfig }) {
  const [selected, setSelected] = useState(config.templates[0]?.key ?? '')
  const template = config.templates.find((t) => t.key === selected)

  return (
    <section>
      <h3>Playbooks</h3>
      <label>
        Template{' '}
        <select value={selected} onChange={(e) => setSelected(e.target.value)}>
          {config.templates.map((t) => (
            <option key={t.key} value={t.key}>
              {t.name}
            </option>
          ))}
        </select>
      </label>
      {template && <TemplatePlaybook key={template.key} slug={slug} config={config} template={template} />}
    </section>
  )
}
