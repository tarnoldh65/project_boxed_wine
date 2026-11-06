import { useMembers, useTenantConfig } from '../api/queries'
import { Skeleton } from '../Skeleton'
import { useTenant } from '../tenant'
import { TokensPanel } from '../TokensPanel'
import { EscalationEditor } from './EscalationEditor'
import { FieldsEditor } from './FieldsEditor'
import { PlaybookEditor } from './PlaybookEditor'
import { RegimesEditor } from './RegimesEditor'
import { SeverityEditor } from './SeverityEditor'
import { StatesEditor } from './StatesEditor'
import { TeamsEditor } from './TeamsEditor'
import { TemplatesEditor } from './TemplatesEditor'

const WORKFLOW_SECTIONS = [
  ['states', 'Lifecycle states'],
  ['fields', 'Custom fields'],
  ['severity', 'Severity and SLAs'],
  ['escalation', 'Escalation rules'],
  ['teams', 'Teams'],
  ['templates', 'Case templates'],
  ['playbooks', 'Playbooks'],
  ['regimes', 'Notification regimes'],
]

export function Settings() {
  const { slug, permissions } = useTenant()
  const canConfigure = permissions.includes('config:manage')
  const canManageTokens = permissions.includes('tokens:manage')
  const config = useTenantConfig(slug, canConfigure)
  const members = useMembers(slug)
  const sections = [
    ...(canConfigure ? WORKFLOW_SECTIONS : []),
    ...(canManageTokens ? [['tokens', 'API tokens']] : []),
  ]

  return (
    <div className="settings-layout">
      <nav className="settings-nav" aria-label="Settings sections">
        {sections.map(([id, label]) => (
          <a key={id} href={`#${id}`}>
            {label}
          </a>
        ))}
      </nav>
      <div className="settings">
        {config.isError && <p role="alert">{config.error.message}</p>}
        {canConfigure && !config.isError && !(config.data && members.data) && <Skeleton lines={8} />}
        {config.data && members.data && (
          <section>
            <h2>Workflow settings</h2>
            <div id="states" className="panel">
              <StatesEditor slug={slug} states={config.data.states} />
            </div>
            <div id="fields" className="panel">
              <FieldsEditor
                slug={slug}
                fields={config.data.custom_fields}
                states={config.data.states}
              />
            </div>
            <div id="severity" className="panel">
              <SeverityEditor slug={slug} severity={config.data.severity} />
            </div>
            <div id="escalation" className="panel">
              <EscalationEditor
                slug={slug}
                rules={config.data.escalation_rules}
                severities={config.data.severity.levels.map((level) => level.key)}
                teams={config.data.teams}
              />
            </div>
            <div id="teams" className="panel">
              <TeamsEditor slug={slug} teams={config.data.teams} members={members.data} />
            </div>
            <div id="templates" className="panel">
              <TemplatesEditor slug={slug} config={config.data} />
            </div>
            <div id="playbooks" className="panel">
              <PlaybookEditor slug={slug} config={config.data} />
            </div>
            <div id="regimes" className="panel">
              <RegimesEditor slug={slug} regimes={config.data.regimes} />
            </div>
          </section>
        )}
        {canManageTokens && (
          <div id="tokens" className="panel">
            <TokensPanel slug={slug} />
          </div>
        )}
      </div>
    </div>
  )
}
