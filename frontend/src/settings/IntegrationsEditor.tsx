import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type ConfluenceSettings, type JiraSettings, type TenantConfig } from '../api/client'
import { load } from '../api/queries'
import { SaveBar } from './SaveBar'
import { useSaveSection } from './editing'

function ConnectionField({
  label,
  names,
  value,
  onChange,
}: {
  label: string
  names: string[]
  value: string
  onChange: (value: string) => void
}) {
  return (
    <label>
      {label} connection{' '}
      <select aria-label={`${label} connection`} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">Off</option>
        {names.map((name) => (
          <option key={name} value={name}>
            {name}
          </option>
        ))}
      </select>
    </label>
  )
}

export function IntegrationsEditor({ slug, config }: { slug: string; config: TenantConfig }) {
  const connections = useQuery({
    queryKey: ['connections', slug],
    queryFn: load('connections', () =>
      api.GET('/api/tenants/{slug}/config/connections', { params: { path: { slug } } }),
    ),
  })
  const named = (type: string) =>
    connections.data?.filter((c) => c.type === type).map((c) => c.name) ?? []
  const [jira, setJira] = useState({
    connection: config.jira?.connection ?? '',
    project_key: config.jira?.project_key ?? '',
    issue_type: config.jira?.issue_type ?? 'Task',
  })
  const [wiki, setWiki] = useState({
    connection: config.confluence?.connection ?? '',
    space_key: config.confluence?.space_key ?? '',
    parent_page_id: config.confluence?.parent_page_id ?? '',
  })
  const saveJira = useSaveSection(slug, (body: JiraSettings | null) =>
    api.PUT('/api/tenants/{slug}/config/jira', { params: { path: { slug } }, body }),
  )
  const saveWiki = useSaveSection(slug, (body: ConfluenceSettings | null) =>
    api.PUT('/api/tenants/{slug}/config/confluence', { params: { path: { slug } }, body }),
  )

  return (
    <>
      <h3>Integrations</h3>
      <p>
        Connections are set by the platform operator in the deployment secret (see
        docs/integrations.md). Restricted and TLP:RED cases are never sent out.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          saveJira.mutate(jira.connection ? jira : null)
        }}
      >
        <h4>Jira</h4>
        <ConnectionField
          label="Jira"
          names={named('jira')}
          value={jira.connection}
          onChange={(connection) => setJira({ ...jira, connection })}
        />
        <label>
          Project key{' '}
          <input
            aria-label="Jira project key"
            value={jira.project_key}
            onChange={(e) => setJira({ ...jira, project_key: e.target.value })}
          />
        </label>
        <label>
          Issue type{' '}
          <input
            aria-label="Jira issue type"
            value={jira.issue_type}
            onChange={(e) => setJira({ ...jira, issue_type: e.target.value })}
          />
        </label>
        <SaveBar save={saveJira} />
      </form>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          saveWiki.mutate(
            wiki.connection ? { ...wiki, parent_page_id: wiki.parent_page_id.trim() || null } : null,
          )
        }}
      >
        <h4>Confluence</h4>
        <ConnectionField
          label="Confluence"
          names={named('confluence')}
          value={wiki.connection}
          onChange={(connection) => setWiki({ ...wiki, connection })}
        />
        <label>
          Space key{' '}
          <input
            aria-label="Confluence space key"
            value={wiki.space_key}
            onChange={(e) => setWiki({ ...wiki, space_key: e.target.value })}
          />
        </label>
        <label>
          Parent page id{' '}
          <input
            aria-label="Confluence parent page id"
            placeholder="optional"
            value={wiki.parent_page_id}
            onChange={(e) => setWiki({ ...wiki, parent_page_id: e.target.value })}
          />
        </label>
        <SaveBar save={saveWiki} />
      </form>
    </>
  )
}
