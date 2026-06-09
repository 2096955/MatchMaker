import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { getDatasets, getProviders, deleteDataset, triggerIngest } from '../../api'
import ConfirmModal from '../../components/ConfirmModal'

const FORMAT_BADGE = {
  csv: 'badge-green', xlsx: 'badge-blue', json: 'badge-amber',
  xml: 'badge-indigo', parquet: 'badge-gray',
}

export default function DatasetList() {
  const [datasets, setDatasets]   = useState([])
  const [providers, setProviders] = useState([])
  const [search, setSearch]       = useState('')
  const [provFilter, setProvFilter] = useState('')
  const [loading, setLoading]     = useState(false)
  const [error, setError]         = useState('')
  const [toDelete, setToDelete]   = useState(null)
  const [ingestState, setIngestState] = useState({}) // { [dataset_id]: 'loading'|'ok'|'error' }
  const navigate = useNavigate()

  const load = useCallback(async (q = '', pid = '') => {
    setLoading(true); setError('')
    try {
      const { data } = await getDatasets(q, pid)
      setDatasets(data)
    } catch {
      setError('Failed to load datasets')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    getProviders().then(({ data }) => setProviders(data)).catch(() => {})
  }, [load])

  const handleSearch = e => {
    const q = e.target.value; setSearch(q)
    if (q.length === 0 || q.length >= 2) load(q, provFilter)
  }
  const handleProvFilter = e => {
    const pid = e.target.value; setProvFilter(pid)
    load(search, pid)
  }

  const handleIngest = async (dataset_id) => {
    setIngestState(s => ({ ...s, [dataset_id]: 'loading' }))
    try {
      await triggerIngest(dataset_id)
      setIngestState(s => ({ ...s, [dataset_id]: 'ok' }))
      setTimeout(() => setIngestState(s => { const n = { ...s }; delete n[dataset_id]; return n }), 3000)
    } catch (err) {
      setIngestState(s => ({ ...s, [dataset_id]: err.response?.data?.error || 'Ingest failed' }))
      setTimeout(() => setIngestState(s => { const n = { ...s }; delete n[dataset_id]; return n }), 4000)
    }
  }

  const handleDelete = async () => {
    try {
      await deleteDataset(toDelete.dataset_id)
      setToDelete(null)
      load(search, provFilter)
    } catch (err) {
      setError(err.response?.data?.error || 'Delete failed')
      setToDelete(null)
    }
  }

  return (
    <>
      <div className="page-header" style={{ maxWidth: 946 }}>
        <div>
          <div className="page-title">Third-Party Datasets</div>
          <div className="page-sub">Manage dataset registrations and schema</div>
        </div>
        <button className="btn btn-primary" onClick={() => navigate('/datasets/new')}>
          + Add Dataset
        </button>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      <div className="card" style={{ maxWidth: 946 }}>
        <div className="card-header">
          <span>{datasets.length} dataset{datasets.length !== 1 ? 's' : ''}</span>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <select className="f-select" value={provFilter} onChange={handleProvFilter}
              style={{ width: 180 }}>
              <option value="">All Providers</option>
              {providers.map(p => (
                <option key={p.provider_id} value={p.provider_id}>{p.provider_name}</option>
              ))}
            </select>
            <div className="search-bar">
              <span className="search-icon">🔍</span>
              <input placeholder="Search datasets…" value={search} onChange={handleSearch} />
            </div>
          </div>
        </div>

        {loading ? (
          <div style={{ padding: 40, textAlign: 'center' }}><span className="spinner" /></div>
        ) : datasets.length === 0 ? (
          <div className="empty-state">
            <div className="empty-icon">📦</div>
            <p>No datasets found. Add one to get started.</p>
          </div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Provider</th>
                <th>Dataset Name</th>
                <th>Format</th>
                <th>Frequency</th>
                <th>Columns</th>
                <th>Ver</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {datasets.map(d => (
                <tr key={d.dataset_id}>
                  <td><span className="badge badge-indigo">{d.provider_name}</span></td>
                  <td>
                    <strong>{d.dataset_name}</strong>
                    {d.dataset_desc && <div style={{ color: '#9ca3af', fontSize: 11, marginTop: 2 }}>{d.dataset_desc}</div>}
                  </td>
                  <td>
                    {d.data_format && (
                      <span className={`badge ${FORMAT_BADGE[d.data_format] || 'badge-gray'}`}>
                        {d.data_format.toUpperCase()}
                      </span>
                    )}
                  </td>
                  <td>{d.frequency || '—'}</td>
                  <td>
                    <span className="badge badge-gray">{d.columns?.length || 0} cols</span>
                  </td>
                  <td><span className="badge badge-blue">v{d.version}</span></td>
                  <td>
                    <div className="action-cell" style={{ flexWrap: 'wrap', gap: 4 }}>
                      <button className="btn btn-ghost btn-sm"
                        onClick={() => navigate(`/datasets/${d.dataset_id}/edit`)}>
                        Edit
                      </button>
                      <button className="btn btn-ghost btn-sm"
                        style={{ color: '#7c3aed', borderColor: '#7c3aed' }}
                        onClick={() => navigate(`/datasets/${d.dataset_id}/transforms`)}
                        title="Configure column transformations">
                        &#128295; Transforms
                      </button>
                      <button className="btn btn-danger btn-sm"
                        onClick={() => setToDelete(d)}>
                        Delete
                      </button>
                      <button
                        className="btn btn-ghost btn-sm"
                        style={{ color: '#2563eb', borderColor: '#2563eb' }}
                        disabled={ingestState[d.dataset_id] === 'loading'}
                        aria-busy={ingestState[d.dataset_id] === 'loading'}
                        onClick={() => handleIngest(d.dataset_id)}
                        title="Trigger ingestion run for this dataset"
                      >
                        {ingestState[d.dataset_id] === 'loading'
                          ? <><span className="spinner" style={{ width: 11, height: 11 }} aria-hidden="true" /> Running…</>
                          : <>▶ Ingest</>}
                      </button>
                      {ingestState[d.dataset_id] === 'ok' && (
                        <span className="badge badge-green" style={{ fontSize: 11 }}>&#10003; Triggered</span>
                      )}
                      {ingestState[d.dataset_id] && ingestState[d.dataset_id] !== 'loading' && ingestState[d.dataset_id] !== 'ok' && (
                        <span className="badge" style={{ fontSize: 11, color: '#dc2626', borderColor: '#dc2626', background: '#fff8f8' }}>
                          ✗ {ingestState[d.dataset_id]}
                        </span>
                      )}
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
          title="Delete Dataset"
          message={`Delete dataset "${toDelete.dataset_name}"? All column schema for this dataset will also be removed.`}
          onConfirm={handleDelete}
          onCancel={() => setToDelete(null)}
        />
      )}
    </>
  )
}
