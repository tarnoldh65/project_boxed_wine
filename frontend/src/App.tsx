import { useQuery } from '@tanstack/react-query'
import { useEffect, type ReactNode } from 'react'
import {
  Navigate,
  NavLink,
  Outlet,
  Route,
  Routes,
  useNavigate,
  useParams,
} from 'react-router'
import { api, type Me } from './api/client'
import { CasePage } from './cases/CasePage'
import { CasesPage } from './cases/CasesPage'
import { ObservablePage } from './cases/ObservablePage'
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
  return (
    <Routes>
      <Route path="/t/:slug" element={<TenantLayout me={me.data} />}>
        <Route index element={<CasesPage />} />
        <Route path="cases/:number" element={<CasePage />} />
        <Route path="observables/:observableId" element={<ObservablePage />} />
        <Route path="settings" element={<Settings />} />
      </Route>
      <Route path="*" element={<Home me={me.data} />} />
    </Routes>
  )
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

function storedTenant(): string | null {
  try {
    return localStorage.getItem(TENANT_KEY)
  } catch {
    return null
  }
}

function Home({ me }: { me: Me }) {
  const stored = storedTenant()
  const tenant = me.tenants.find((t) => t.slug === stored) ?? me.tenants[0]
  if (tenant) return <Navigate to={`/t/${tenant.slug}`} replace />
  return (
    <Shell actions={<UserActions me={me} />}>
      <p>You do not have access to any tenant. Ask an administrator to add you to a group.</p>
    </Shell>
  )
}

function UserActions({ me }: { me: Me }) {
  async function signOut() {
    const { data } = await api.POST('/api/auth/logout')
    window.location.assign(data?.logout_url ?? '/')
  }
  return (
    <>
      <span>{me.display_name}</span>
      <button type="button" onClick={signOut}>
        Sign out
      </button>
    </>
  )
}

function TenantLayout({ me }: { me: Me }) {
  const { slug } = useParams()
  const navigate = useNavigate()
  const tenant = me.tenants.find((t) => t.slug === slug)

  useEffect(() => {
    if (!tenant) return
    try {
      localStorage.setItem(TENANT_KEY, tenant.slug)
    } catch {
      // Remembering the tenant is a convenience only.
    }
  }, [tenant])

  if (!tenant) return <Navigate to="/" replace />
  const isAdmin = ['config:manage', 'tokens:manage'].some((p) => tenant.permissions.includes(p))

  const actions = (
    <>
      <label>
        Tenant{' '}
        <select value={tenant.slug} onChange={(e) => navigate(`/t/${e.target.value}`)}>
          {me.tenants.map((t) => (
            <option key={t.slug} value={t.slug}>
              {t.name}
            </option>
          ))}
        </select>
      </label>
      <UserActions me={me} />
    </>
  )

  return (
    <Shell actions={actions}>
      <h1>{tenant.name}</h1>
      <nav className="tabs">
        <NavLink to={`/t/${tenant.slug}`} end>
          Cases
        </NavLink>
        {isAdmin && <NavLink to={`/t/${tenant.slug}/settings`}>Settings</NavLink>}
      </nav>
      <Outlet key={tenant.slug} context={tenant} />
    </Shell>
  )
}
