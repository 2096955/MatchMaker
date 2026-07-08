import { NavLink } from 'react-router-dom'

const NAV = [
  { section: 'Data Ingestion', links: [
    { to: '/providers',  icon: '🏢', label: 'Providers'          },
    { to: '/catalogue',  icon: '📚', label: 'Vendor Catalogue'   },
    { to: '/datasets',   icon: '📦', label: 'Datasets'            },
    { to: '/ingestion',  icon: '⚡', label: 'Ingestion Console'   },
    { to: '/matching-test', icon: '🧪', label: 'Matching Test' },
  ]},
  { section: 'Reports', links: [
    { to: '/reports', icon: '📋', label: 'Ingestion Reports' },
  ]},
  { section: 'Administration', links: [
    { to: '/admin/roles', icon: '🛡️', label: 'Roles'  },
    { to: '/admin/users', icon: '👤', label: 'Users'  },
  ]},
]

export default function Layout({ children }) {
  return (
    <div className="shell">
      <header className="app-header">
        <div className="header-logo">DI</div>
        <div>
          <div className="header-title">Data Ingestion Framework</div>
          <div className="header-sub">Third-Party Data Management</div>
        </div>
        <div className="header-spacer" />
        <div className="header-user">A</div>
      </header>

      <div className="body-wrap">
        <nav className="app-sidebar">
          {NAV.map(({ section, links }) => (
            <div key={section}>
              <div className="sidebar-section-label">{section}</div>
              {links.map(({ to, icon, label }) => (
                <NavLink
                  key={to}
                  to={to}
                  className={({ isActive }) => `sidebar-link${isActive ? ' active' : ''}`}
                >
                  <span className="sidebar-icon">{icon}</span>
                  {label}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>

        <main className="app-content">{children}</main>
      </div>

      <footer className="app-footer">
        © 2026 Cognizant · Data Ingestion Framework
      </footer>
    </div>
  )
}
