import { useQuery } from '@tanstack/react-query'
import { api, type TenantAccess } from '../api/client'
import { TokensPanel } from '../TokensPanel'
import { EscalationEditor } from './EscalationEditor'
import { FieldsEditor } from './FieldsEditor'
import { SeverityEditor } from './SeverityEditor'
import { StatesEditor } from './StatesEditor'

export function Settings({ tenant }: { tenant: TenantAccess }) {
  const { slug } = tenant
  const canConfigure = tenant.permissions.includes('config:manage')
  const config = useQuery({
    queryKey: ['config', slug],
    queryFn: async () => {
      const { data, response } = await api.GET('/api/tenants/{slug}/config', {
        params: { path: { slug } },
      })
      if (!data) throw new Error(`Could not load settings (${response.status})`)
      return data
    },
    enabled: canConfigure,
  })

  return (
    <>
      {config.isError && <p role="alert">{config.error.message}</p>}
      {config.data && (
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
        </section>
      )}
      {tenant.permissions.includes('tokens:manage') && <TokensPanel slug={slug} />}
    </>
  )
}
