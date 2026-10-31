import { useState } from 'react'
import { api, type CustomField, type FieldType, type State } from '../api/client'
import { SaveBar } from './SaveBar'
import { removeAt, splitList, updateAt, useSaveSection } from './editing'

const TYPES: FieldType[] = ['text', 'picklist', 'date', 'user', 'number']

type Row = Omit<CustomField, 'options'> & { optionsText: string }

function toRow(field: CustomField): Row {
  const { options, ...rest } = field
  return { ...rest, optionsText: options.join(', ') }
}

function toField({ optionsText, ...rest }: Row): CustomField {
  return { ...rest, options: rest.type === 'picklist' ? splitList(optionsText) : [] }
}

export function FieldsEditor({
  slug,
  fields,
  states,
}: {
  slug: string
  fields: CustomField[]
  states: State[]
}) {
  const [draft, setDraft] = useState(() => fields.map(toRow))
  const save = useSaveSection(slug, (body: CustomField[]) =>
    api.PUT('/api/tenants/{slug}/config/custom-fields', { params: { path: { slug } }, body }),
  )

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        save.mutate(draft.map(toField))
      }}
    >
      <h3>Custom fields</h3>
      <table>
        <thead>
          <tr>
            <th>Key</th>
            <th>Label</th>
            <th>Type</th>
            <th>Options</th>
            <th>Required from</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {draft.map((field, i) => (
            <tr key={i}>
              <td>
                <input
                  aria-label={`Field ${i + 1} key`}
                  value={field.key}
                  onChange={(e) => setDraft(updateAt(draft, i, { key: e.target.value }))}
                />
              </td>
              <td>
                <input
                  aria-label={`Field ${i + 1} label`}
                  value={field.label}
                  onChange={(e) => setDraft(updateAt(draft, i, { label: e.target.value }))}
                />
              </td>
              <td>
                <select
                  aria-label={`Field ${i + 1} type`}
                  value={field.type}
                  onChange={(e) =>
                    setDraft(updateAt(draft, i, { type: e.target.value as FieldType }))
                  }
                >
                  {TYPES.map((type) => (
                    <option key={type}>{type}</option>
                  ))}
                </select>
              </td>
              <td>
                {field.type === 'picklist' && (
                  <input
                    aria-label={`Field ${i + 1} options`}
                    placeholder="comma separated"
                    value={field.optionsText}
                    onChange={(e) => setDraft(updateAt(draft, i, { optionsText: e.target.value }))}
                  />
                )}
              </td>
              <td>
                <select
                  aria-label={`Field ${i + 1} required from`}
                  value={field.required_from ?? ''}
                  onChange={(e) =>
                    setDraft(updateAt(draft, i, { required_from: e.target.value || null }))
                  }
                >
                  <option value="">Never required</option>
                  {states.map((state) => (
                    <option key={state.key} value={state.key}>
                      {state.name}
                    </option>
                  ))}
                </select>
              </td>
              <td>
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
          setDraft([
            ...draft,
            { key: '', label: '', type: 'text', optionsText: '', required_from: null },
          ])
        }
      >
        Add field
      </button>
      <SaveBar save={save} />
    </form>
  )
}
