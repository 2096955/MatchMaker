import { useState, useEffect, useCallback } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { getCatalogueProducts, getCatalogueVendors } from '../../api'

const PAGE_LIMIT = 50

export default function CatalogueList() {
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()

  const [vendors, setVendors]               = useState([])
  const [vendor, setVendor]                 = useState(searchParams.get('vendor') || 'lseg')
  const [modifiedSince, setModifiedSince]   = useState('')
  const [items, setItems]                   = useState([])
  const [nextCursor, setNextCursor]         = useState(null)
  const [loading, setLoading]               = useState(false)
  const [loadingMore, setLoadingMore]       = useState(false)
  const [error, setError]                   = useState('')

  // Keep the vendor query param in sync so deep links (e.g. from the Providers
  // page) and refresh both land on the right view.
  useEffect(() => {
    if (searchParams.get('vendor') !== vendor) {
      setSearchParams({ vendor }, { replace: true })
    }
  }, [vendor, searchParams, setSearchParams])

  useEffect(() => {
    getCatalogueVendors()
      .then(({ data }) => setVendors(data.vendors || []))
      .catch(() => setVendors(['lseg', 'spglobal', 'bloomberg', 'ice', 'factset']))
  }, [])

  // First page on vendor/filter change. Cursor reset is intentional — changing
  // the filter invalidates the previous cursor.
  const loadFirstPage = useCallback(async () => {
    setLoading(true); setError(''); setItems([]); setNextCursor(null)
    try {
      const params = { vendor, limit: PAGE_LIMIT }
      if (modifiedSince) {
        params.modified_since = `${modifiedSince}T00:00:00Z`
      }
      const { data } = await getCatalogueProducts(params)
      setItems(data.items || [])
      setNextCursor(data.next_cursor || null)
    } catch (err) {
      const msg = err.response?.data?.error || 'Failed to load catalogue'
      setError(msg)
    } finally {
      setLoading(false)
    }
  }, [vendor, modifiedSince])

  useEffect(() => { loadFirstPage() }, [loadFirstPage])

  const handleLoadMore = async () => {
    if (!nextCursor) return
    setLoadingMore(true); setError('')
    try {
      const params = { vendor, limit: PAGE_LIMIT, cursor: nextCursor }
      if (modifiedSince) {
        params.modified_since = `${modifiedSince}T00:00:00Z`
      }
      const { data } = await getCatalogueProducts(params)
      setItems(prev => [...prev, ...(data.items || [])])
      setNextCursor(data.next_cursor || null)
    } catch (err) {
      setError(err.response?.data?.error || 'Failed to load next page')
    } finally {
      setLoadingMore(false)
    }
  }

  return (
    <>
      <div className="page-header" style={{ maxWidth: 946 }}>
        <div>
          <div className="page-title">Vendor Catalogue</div>
          <div className="page-sub">Browse the vendor's product catalogue (mock-backed; swaps to S3/Glue on AWS)</div>
        </div>
      </div>

      {error && <div className="alert alert-error" style={{ maxWidth: 946 }}>{error}</div>}

      <div className="card" style={{ maxWidth: 946 }}>
        <div className="card-header" style={{ gap: 12, flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
              <span style={{ color: '#6b7280' }}>Vendor</span>
              <select
                value={vendor}
                onChange={e => setVendor(e.target.value)}
                style={{ padding: '4px 8px', fontSize: 13, border: '1px solid #d1d5db', borderRadius: 4 }}
              >
                {vendors.map(v => <option key={v} value={v}>{v}</option>)}
              </select>
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
              <span style={{ color: '#6b7280' }}>Modified since</span>
              <input
                type="date"
                value={modifiedSince}
                onChange={e => setModifiedSince(e.target.value)}
                style={{ padding: '4px 8px', fontSize: 13, border: '1px solid #d1d5db', borderRadius: 4 }}
              />
              {modifiedSince && (
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={() => setModifiedSince('')}
                  style={{ padding: '2px 8px' }}
                >Clear</button>
              )}
            </label>
          </div>
          <span style={{ fontSize: 13, color: '#6b7280' }}>
            {items.length} {items.length === 1 ? 'product' : 'products'}
            {nextCursor ? ' (more available)' : ''}
          </span>
        </div>

        {loading ? (
          <div style={{ padding: 40, textAlign: 'center' }}><span className="spinner" aria-label="Loading" /></div>
        ) : items.length === 0 ? (
          <div className="empty-state">
            <div className="empty-icon">📚</div>
            <p>{error ? 'No catalogue available.' : 'No products match the current filter.'}</p>
          </div>
        ) : (
          <>
            <table>
              <thead>
                <tr>
                  <th>Vendor Product Ref</th>
                  <th>Title</th>
                  <th>IRI</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {items.map(p => (
                  <tr
                    key={p.iri}
                    onClick={() => navigate(`/catalogue/${p.vendor}/${encodeURIComponent(p.vendor_product_ref)}`)}
                    style={{ cursor: 'pointer' }}
                  >
                    <td><strong>{p.vendor_product_ref}</strong></td>
                    <td style={{ color: '#374151' }}>{p.title}</td>
                    <td style={{ fontFamily: 'monospace', fontSize: 12, color: '#6b7280', wordBreak: 'break-all' }}>
                      {p.iri}
                    </td>
                    <td>
                      <div className="action-cell">
                        <button
                          className="btn btn-ghost btn-sm"
                          onClick={e => {
                            e.stopPropagation()
                            navigate(`/catalogue/${p.vendor}/${encodeURIComponent(p.vendor_product_ref)}`)
                          }}
                        >
                          View
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {nextCursor && (
              <div style={{ padding: 12, textAlign: 'center', borderTop: '1px solid #f3f4f6' }}>
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={handleLoadMore}
                  disabled={loadingMore}
                >
                  {loadingMore ? 'Loading…' : 'Load more'}
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </>
  )
}
