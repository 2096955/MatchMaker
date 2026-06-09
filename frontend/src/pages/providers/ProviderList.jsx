import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { getProviders, deleteProvider } from '../../api'
import ConfirmModal from '../../components/ConfirmModal'

// Best-effort map from a free-text provider_name to a VendorId enum value.
// Returns null when there's no confident match — the UI then hides the
// "Catalogue" link rather than producing a dead-end. Add aliases here as
// new vendors land (or replace with a join column on tp_provider).
const VENDOR_ALIASES = {
  lseg:      ['lseg', 'refinitiv', 'london stock exchange', 'thomson reuters'],
  spglobal:  ['s&p global', 's&p', 'sp global', 'spglobal', 'standard & poor'],
  bloomberg: ['bloomberg'],
  ice:       ['ice', 'intercontinental exchange'],
  factset:   ['factset', 'fact set'],
}
function vendorFromProviderName(name) {
  if (!name) return null
  const n = name.toLowerCase()
  for (const [vid, aliases] of Object.entries(VENDOR_ALIASES)) {
    if (aliases.some(a => n.includes(a))) return vid
  }
  return null
}

export default function ProviderList() {
  const [providers, setProviders]   = useState([])
  const [search, setSearch]         = useState('')
  const [loading, setLoading]       = useState(false)
  const [error, setError]           = useState('')
  const [toDelete, setToDelete]     = useState(null)
  const navigate = useNavigate()

  const load = useCallback(async (q = '') => {
    setLoading(true); setError('')
    try {
      const { data } = await getProviders(q)
      setProviders(data)
    } catch {
      setError('Failed to load providers')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const handleSearch = e => {
    const q = e.target.value
    setSearch(q)
    if (q.length === 0 || q.length >= 2) load(q)
  }

  const handleDelete = async () => {
    try {
      await deleteProvider(toDelete.provider_id)
      setToDelete(null)
      load(search)
    } catch (err) {
      setError(err.response?.data?.error || 'Delete failed')
      setToDelete(null)
    }
  }

  return (
    <>
      <div className="page-header" style={{ maxWidth: 946 }}>
        <div>
          <div className="page-title">Third-Party Providers</div>
          <div className="page-sub">Manage data providers and their metadata</div>
        </div>
        <button className="btn btn-primary" onClick={() => navigate('/providers/new')}>
          + Add Provider
        </button>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      <div className="card" style={{ maxWidth: 946 }}>
        <div className="card-header">
          <span>{providers.length} provider{providers.length !== 1 ? 's' : ''}</span>
          <div className="search-bar">
            <span className="search-icon">🔍</span>
            <input
              placeholder="Search providers…"
              value={search}
              onChange={handleSearch}
            />
          </div>
        </div>

        {loading ? (
          <div style={{ padding: 40, textAlign: 'center' }}><span className="spinner" /></div>
        ) : providers.length === 0 ? (
          <div className="empty-state">
            <div className="empty-icon">🏢</div>
            <p>No providers found. Add one to get started.</p>
          </div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Provider Name</th>
                <th>Description</th>
                <th>Website</th>
                <th>Ver</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {providers.map(p => (
                <tr key={p.provider_id}>
                  <td><strong>{p.provider_name}</strong></td>
                  <td style={{ maxWidth: 280, color: '#6b7280' }}>
                    {p.provider_desc || '—'}
                  </td>
                  <td>
                    {p.provider_website
                      ? <a href={p.provider_website} target="_blank" rel="noreferrer"
                           style={{ color: '#6366f1' }}>
                          {p.provider_website.replace(/^https?:\/\//, '')}
                        </a>
                      : '—'}
                  </td>
                  <td><span className="badge badge-indigo">v{p.version}</span></td>
                  <td>
                    <div className="action-cell">
                      {(() => {
                        const vid = vendorFromProviderName(p.provider_name)
                        return vid ? (
                          <button className="btn btn-ghost btn-sm"
                            onClick={() => navigate(`/catalogue?vendor=${vid}`)}
                            title={`View ${vid} catalogue`}>
                            Catalogue
                          </button>
                        ) : null
                      })()}
                      <button className="btn btn-ghost btn-sm"
                        onClick={() => navigate(`/providers/${p.provider_id}/edit`)}>
                        Edit
                      </button>
                      <button className="btn btn-danger btn-sm"
                        onClick={() => setToDelete(p)}>
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {toDelete && (
        <ConfirmModal
          title="Delete Provider"
          message={`Delete "${toDelete.provider_name}"? This will also delete all associated datasets and columns.`}
          onConfirm={handleDelete}
          onCancel={() => setToDelete(null)}
        />
      )}
    </>
  )
}
