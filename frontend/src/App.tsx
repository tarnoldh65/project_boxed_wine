import { useQuery } from '@tanstack/react-query'
import { CircleUser, LayoutGrid, Search as SearchIcon } from 'lucide-react'
import { lazy, Suspense, useEffect, type FormEvent, type ReactNode } from 'react'
import {
  Link,
  Navigate,
  NavLink,
  Outlet,
  Route,
  Routes,
  useNavigate,
  useParams,
} from 'react-router'
import { api, type Me, type TenantAccess } from './api/client'
import { Menu } from './Menu'
import { Skeleton } from './Skeleton'
import { ThemeControl } from './ThemeControl'
import { FRAMES, LAUNCHABLE } from './workspace/layout'
import { useWorkspace, WorkspaceContext, type Workspace as WorkspaceState } from './workspace/useWorkspace'
import { Workspace } from './workspace/Workspace'

const TENANT_KEY = 'casemgr.tenant'
const Settings = lazy(() =>
  import('./settings/Settings').then((module) => ({ default: module.Settings })),
)

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
        <Route index element={<Workspace />} />
        <Route
          path="settings"
          element={
            <Suspense fallback={<Skeleton lines={8} />}>
              <Settings />
            </Suspense>
          }
        />
        <Route path="*" element={<Workspace />} />
      </Route>
      <Route path="*" element={<Home me={me.data} />} />
    </Routes>
  )
}

function Shell({
  actions,
  home = '/',
  children,
}: {
  actions?: ReactNode
  home?: string
  children?: ReactNode
}) {
  return (
    <div className="shell">
      <header className="shell-header">
        <Link className="brand" to={home}>
          Case Manager
        </Link>
        <div className="shell-actions">
          {actions}
          <ThemeControl />
        </div>
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
    <Shell actions={<UserMenu me={me} />}>
      <p>You do not have access to any tenant. Ask an administrator to add you to a group.</p>
    </Shell>
  )
}

function UserMenu({ me }: { me: Me }) {
  async function signOut() {
    const { data } = await api.POST('/api/auth/logout')
    window.location.assign(data?.logout_url ?? '/')
  }
  return (
    <Menu
      label={me.display_name}
      trigger={
        <>
          <CircleUser aria-hidden size={16} /> {me.display_name}
        </>
      }
    >
      <button type="button" className="ghost" onClick={signOut}>
        Sign out
      </button>
    </Menu>
  )
}

function FramesMenu({ workspace }: { workspace: WorkspaceState }) {
  return (
    <Menu
      label="Frames"
      trigger={
        <>
          <LayoutGrid aria-hidden size={16} /> Frames
        </>
      }
    >
      {LAUNCHABLE.map((id) => (
        <label key={id}>
          <input
            type="checkbox"
            checked={workspace.state.open.includes(id)}
            onChange={() => workspace.toggleOpen(id)}
          />
          {FRAMES[id].title}
        </label>
      ))}
      <hr />
      <button type="button" className="ghost" onClick={workspace.reset}>
        Reset layout
      </button>
    </Menu>
  )
}

function GlobalSearch({ slug }: { slug: string }) {
  const navigate = useNavigate()

  function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const q = String(new FormData(event.currentTarget).get('q')).trim()
    event.currentTarget.reset()
    if (q) navigate(`/t/${slug}/search?q=${encodeURIComponent(q)}`)
  }

  return (
    <form className="global-search" role="search" onSubmit={search}>
      <SearchIcon aria-hidden size={16} />
      <input
        name="q"
        type="search"
        maxLength={500}
        aria-label="Search everything"
        placeholder="Search cases, notes, evidence"
      />
    </form>
  )
}

function TenantLayout({ me }: { me: Me }) {
  const { slug } = useParams()
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
  return <TenantShell key={tenant.slug} me={me} tenant={tenant} />
}

function TenantShell({ me, tenant }: { me: Me; tenant: TenantAccess }) {
  const navigate = useNavigate()
  const workspace = useWorkspace(me.id, tenant.slug)
  const isAdmin = ['config:manage', 'tokens:manage'].some((p) => tenant.permissions.includes(p))

  const actions = (
    <>
      <GlobalSearch slug={tenant.slug} />
      <label className="tenant-switch">
        <span className="sr-only">Tenant</span>
        <select value={tenant.slug} onChange={(e) => navigate(`/t/${e.target.value}`)}>
          {me.tenants.map((t) => (
            <option key={t.slug} value={t.slug}>
              {t.name}
            </option>
          ))}
        </select>
      </label>
      <FramesMenu workspace={workspace} />
      {isAdmin && (
        <>
          <NavLink className="top-link" to={`/t/${tenant.slug}`} end>
            Workspace
          </NavLink>
          <NavLink className="top-link" to={`/t/${tenant.slug}/settings`}>
            Settings
          </NavLink>
        </>
      )}
      <UserMenu me={me} />
    </>
  )

  return (
    <Shell actions={actions} home={`/t/${tenant.slug}`}>
      <h1 className="sr-only">{tenant.name}</h1>
      <WorkspaceContext value={workspace}>
        <Outlet context={tenant} />
      </WorkspaceContext>
    </Shell>
  )
}
