import { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { getProviders, getDatasets, getReports } from '../../api'

const STATUS_OPTIONS = ['success', 'failed', 'partial', 'skipped', 'in_progress']

function StatusBadge({ status }) {
  switch (status) {
    case 'success':
      return <span className="badge badge-green">&#10003; Success</span>
    case 'failed':
      return (
        <span className="badge" style={{ borderColor: '#dc2626', color: '#dc2626', background: '#fff8f8' }}>
          &#10007; Failed
        </span>
      )
    case 'partial':
      return <span className="badge badge-amber">&#9888; Partial</span>
    case 'skipped':
      return <span className="badge badge-gray">&#8212; Skipped</span>
    case 'in_progress':
      return <span className="badge badge-blue">&#10711; Running</span>
    default:
      return <span className="badge badge-gray">{status || '—'}</span>
  }
}

function truncate(str, len = 60) {
  if (!str) return '—'
  return str.length > len ? str.slice(0, len) + '…' : str
}

export default function ReportList() {
  const [providers, setProviders]   = useState([])
  const [datasets, setDatasets]     = useState([])
  const [reports, setReports]       = useState([])
  const [loading, setLoading]       = useState(false)
  const [error, setError]           = useState('')

  // filter state
  const [provFilter, setProvFilter] = useState('')
  const [dsFilter, setDsFilter]     = useState('')
  const [statuses, setStatuses]     = useState([])
  const [fromDate, setFromDate]     = useState('')
  const [toDate, setToDate]         = useState('')

  // load providers once
  useEffect(() => {
    getProviders().then(({ data }) => setProviders(data)).catch(() => {})
  }, [])

  // reload dataset list whenever provider filter changes
  useEffect(() => {
    if (!provFilter) { setDatasets([]); setDsFilter(''); return }
    getDatasets('', provFilter)
      .then(({ data }) => { setDatasets(data); setDsFilter('') })
      .catch(() => {})
  }, [provFilter])

  const fetchReports = useCallback(async (params) => {
    setLoading(true); setError('')
    try {
      const { data } = await getReports(params)
      setReports(data)
    } catch {
      setError('Failed to load reports')
    } finally {
      setLoading(false)
    }
  }, [])

  // fetch on mount with no filters
  useEffect(() => { fetchReports({}) }, [fetchReports])

  const buildParams = () => {
    const p = {}
    if (provFilter) p.provider_id = provFilter
    if (dsFilter)   p.dataset_id  = dsFilter
    if (statuses.length > 0) p.status = statuses.join(',')
    if (fromDate)   p.from_date   = fromDate
    if (toDate)     p.to_date     = toDate
    return p
  }

  const handleApply = () => fetchReports(buildParams())

  const handleClear = () => {
    setProvFilter(''); setDsFilter(''); setStatuses([])
    setFromDate(''); setToDate('')
    fetchReports({})
  }

  const toggleStatus = (s) => {
    setStatuses(prev =>
      prev.includes(s) ? prev.filter(x => x !== s) : [...prev, s]
    )
  }

  return (
    <>
      <div className="page-header" style={{ position: 'sticky', top: 0, zIndex: 10, background: '#fff' }}>
        <div>
          <div className="page-title">Ingestion Reports</div>
          <div className="page-sub">ETL run history and status</div>
        </div>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {/* Filter bar */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div style={{ padding: '14px 20px', display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end' }}>

            <div className="form-group" style={{ margin: 0, minWidth: 180 }}>
              <label className="form-label" htmlFor="r-provider">Provider</label>
              <select id="r-provider" className="f-select"
                value={provFilter}
                onChange={e => setProvFilter(e.target.value)}>
                <option value="">All Providers</option>
                {providers.map(p => (
                  <option key={p.provider_id} value={p.provider_id}>{p.provider_name}</option>
                ))}
              </select>
            </div>

            <div className="form-group" style={{ margin: 0, minWidth: 200 }}>
              <label className="form-label" htmlFor="r-dataset">Dataset</label>
              <select id="r-dataset" className="f-select"
                value={dsFilter}
                onChange={e => setDsFilter(e.target.value)}
                disabled={!provFilter}>
                <option value="">All Datasets</option>
                {datasets.map(d => (
                  <option key={d.dataset_id} value={d.dataset_id}>{d.dataset_name}</option>
                ))}
              </select>
            </div>

            <div className="form-group" style={{ margin: 0, minWidth: 120 }}>
              <label className="form-label" htmlFor="r-from">From Date</label>
              <input id="r-from" type="date" className="f-input"
                value={fromDate} onChange={e => setFromDate(e.target.value)} />
            </div>

            <div className="form-group" style={{ margin: 0, minWidth: 120 }}>
              <label className="form-label" htmlFor="r-to">To Date</label>
              <input id="r-to" type="date" className="f-input"
                value={toDate} onChange={e => setToDate(e.target.value)} />
            </div>

          </div>

          <div style={{ display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
            <span className="form-label" style={{ marginBottom: 0, whiteSpace: 'nowrap' }}>Status:</span>
            {STATUS_OPTIONS.map(s => (
              <label key={s} style={{ display: 'flex', alignItems: 'center', gap: 5, cursor: 'pointer', fontSize: 13 }}>
                <input type="checkbox"
                  checked={statuses.includes(s)}
                  onChange={() => toggleStatus(s)} />
                {s.replace('_', ' ')}
              </label>
            ))}
          </div>

          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn btn-primary btn-sm" onClick={handleApply}>Apply Filters</button>
            <button className="btn btn-ghost btn-sm" onClick={handleClear}>Clear</button>
          </div>
        </div>
      </div>

      {/* Results */}
      <div className="card">
        {loading ? (
          <div style={{ padding: 40, textAlign: 'center' }}><span className="spinner" aria-label="Loading" /></div>
        ) : (
          <>
            <div className="card-header">
              <span>Showing {reports.length} result{reports.length !== 1 ? 's' : ''}</span>
            </div>

            {reports.length === 0 ? (
              <div className="empty-state">
                <div className="empty-icon">📋</div>
                <p>No ingestion runs match your filters.</p>
              </div>
            ) : (
              <div style={{ overflowX: 'auto' }}>
                <table>
                  <thead>
                    <tr>
                      <th>Run ID</th>
                      <th>Provider</th>
                      <th>Dataset</th>
                      <th>Run Date</th>
                      <th>Status</th>
                      <th>Col Status</th>
                      <th>File Path</th>
                      <th>Records Loaded</th>
                      <th>Notes</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {reports.map(r => (
                      <tr key={r.run_id}>
                        <td style={{ fontFamily: 'monospace', fontSize: 12 }}>{r.run_id}</td>
                        <td>{r.provider_name || '—'}</td>
                        <td><strong>{r.dataset_name || '—'}</strong></td>
                        <td style={{ whiteSpace: 'nowrap', fontSize: 12 }}>
                          {r.run_date ? new Date(r.run_date).toLocaleString() : '—'}
                        </td>
                        <td><StatusBadge status={r.run_status} /></td>
                        <td>
                          {r.column_status
                            ? <span className="badge badge-gray">{r.column_status}</span>
                            : <span style={{ color: '#9ca3af' }}>—</span>}
                        </td>
                        <td style={{ fontSize: 12, maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                          title={r.file_path || ''}>
                          {r.file_path || '—'}
                        </td>
                        <td style={{ textAlign: 'right' }}>
                          {r.records_loaded != null ? r.records_loaded.toLocaleString() : '—'}
                        </td>
                        <td style={{ fontSize: 12, color: '#6b7280', maxWidth: 200 }}>
                          {truncate(r.status_notes)}
                        </td>
                        <td>
                          <Link to={`/reports/${r.run_id}`} className="btn btn-ghost btn-sm">
                            View
                          </Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>
    </>
  )
}
