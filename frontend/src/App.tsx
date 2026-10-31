import { useQuery } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { api, type Me, type TenantAccess } from './api/client'
import { Settings } from './settings/Settings'

const TENANT_KEY = 'casemgr.tenant'

async function fetchMe(): Promise<Me | null> {
  const { data, response } = await api.GET('/api/me')
  if (response.status === 401) return null
  if (!data) throw new Error(`Could not load your profile (${response.status})`)
  return data
}

export default function App() {
  const me = useQuery({ queryKey: ['me'], queryFn: fetchMe })

  if (me.isPending) return <Shell />
  if (me.isError) {
    return (
      <Shell>
        <p role="alert">{me.error.message}</p>
      </Shell>
    )
  }
  if (me.data === null) {
    return (
      <Shell>
        <a className="button" href="/api/auth/login">
          Sign in
        </a>
      </Shell>
    )
  }
  return <Workspace me={me.data} />
}

function Shell({ actions, children }: { actions?: ReactNode; children?: ReactNode }) {
  return (
    <div className="shell">
      <header className="shell-header">
        <span className="brand">Case Manager</span>
        <div className="shell-actions">{actions}</div>
      </header>
      <main className="shell-main">{children}</main>
    </div>
  )
}

function readStoredTenant(): string | null {
  try {
    return localStorage.getItem(TENANT_KEY)
  } catch {
    return null
  }
}

function Workspace({ me }: { me: Me }) {
  const [stored, setStored] = useState(readStoredTenant)
  const tenant = me.tenants.find((t) => t.slug === stored) ?? me.tenants[0]

  function switchTenant(slug: string) {
    setStored(slug)
    try {
      localStorage.setItem(TENANT_KEY, slug)
    } catch {
      // Remembering the tenant is a convenience only.
    }
  }

  async function signOut() {
    const { data } = await api.POST('/api/auth/logout')
    window.location.assign(data?.logout_url ?? '/')
  }

  const actions = (
    <>
      {tenant && (
        <label>
          Tenant{' '}
          <select value={tenant.slug} onChange={(e) => switchTenant(e.target.value)}>
            {me.tenants.map((t) => (
              <option key={t.slug} value={t.slug}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
      )}
      <span>{me.display_name}</span>
      <button type="button" onClick={signOut}>
        Sign out
      </button>
    </>
  )

  return (
    <Shell actions={actions}>
      {tenant ? (
        <TenantHome key={tenant.slug} tenant={tenant} />
      ) : (
        <p>You do not have access to any tenant. Ask an administrator to add you to a group.</p>
      )}
    </Shell>
  )
}

function TenantHome({ tenant }: { tenant: TenantAccess }) {
  const [view, setView] = useState<'cases' | 'settings'>('cases')
  const isAdmin = ['config:manage', 'tokens:manage'].some((p) => tenant.permissions.includes(p))

  return (
    <>
      <h1>{tenant.name}</h1>
      {isAdmin && (
        <nav className="tabs">
          <button type="button" aria-pressed={view === 'cases'} onClick={() => setView('cases')}>
            Cases
          </button>
          <button
            type="button"
            aria-pressed={view === 'settings'}
            onClick={() => setView('settings')}
          >
            Settings
          </button>
        </nav>
      )}
      {view === 'settings' ? (
        <Settings tenant={tenant} />
      ) : (
        <section>
          <h2>Cases</h2>
          <p>No cases yet.</p>
        </section>
      )}
    </>
  )
}
