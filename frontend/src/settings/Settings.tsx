import { useMembers, useTenantConfig } from '../api/queries'
import { useTenant } from '../tenant'
import { TokensPanel } from '../TokensPanel'
import { EscalationEditor } from './EscalationEditor'
import { FieldsEditor } from './FieldsEditor'
import { SeverityEditor } from './SeverityEditor'
import { StatesEditor } from './StatesEditor'
import { TeamsEditor } from './TeamsEditor'
import { TemplatesEditor } from './TemplatesEditor'

export function Settings() {
  const { slug, permissions } = useTenant()
  const canConfigure = permissions.includes('config:manage')
  const config = useTenantConfig(slug, canConfigure)
  const members = useMembers(slug)

  return (
    <>
      {config.isError && <p role="alert">{config.error.message}</p>}
      {config.data && members.data && (
        <section className="settings">
          <h2>Workflow settings</h2>
          <StatesEditor slug={slug} states={config.data.states} />
          <FieldsEditor
            slug={slug}
            fields={config.data.custom_fields}
            states={config.data.states}
          />
          <SeverityEditor slug={slug} severity={config.data.severity} />
          <EscalationEditor
            slug={slug}
            rules={config.data.escalation_rules}
            severities={config.data.severity.levels.map((level) => level.key)}
          />
          <TeamsEditor slug={slug} teams={config.data.teams} members={members.data} />
          <TemplatesEditor slug={slug} config={config.data} />
        </section>
      )}
      {permissions.includes('tokens:manage') && <TokensPanel slug={slug} />}
    </>
  )
}
