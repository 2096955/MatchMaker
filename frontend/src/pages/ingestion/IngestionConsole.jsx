import { useState, useEffect, useCallback, useRef } from 'react'
import { Link } from 'react-router-dom'
import { getProviders, getDatasets, triggerIngest, getReports } from '../../api'

const STATUS_CFG = {
  success:     { cls: 'badge-green', icon: '✓', label: 'Success' },
  failed:      { cls: 'badge-red',   icon: '✗', label: 'Failed' },
  partial:     { cls: 'badge-amber', icon: '⚠', label: 'Partial' },
  skipped:     { cls: 'badge-gray',  icon: '—', label: 'Skipped' },
  in_progress: { cls: 'badge-blue',  icon: '⟳', label: 'Running' },
}

const FORMAT_BADGE = {
  csv: 'badge-green', txt: 'badge-green', xlsx: 'badge-blue',
  json: 'badge-amber', xml: 'badge-indigo', parquet: 'badge-gray',
}

function StatusBadge({ status }) {
  const cfg = STATUS_CFG[status] || { cls: 'badge-gray', icon: '?', label: status }
  return <span className={`badge ${cfg.cls}`}>{cfg.icon} {cfg.label}</span>
}

function RunResult({ run }) {
  return (
    <div style={{
      background: '#fff', border: '1px solid #e5e7eb', borderRadius: 8,
      padding: '12px 16px', marginBottom: 8,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
        <StatusBadge status={run.run_status} />
        {run.column_status && run.column_status !== 'n/a' && (
          <span className={`badge ${run.column_status === 'match' ? 'badge-green' : 'badge-amber'}`}>
            {run.column_status === 'match' ? '≡ Columns match' : `⚠ ${run.column_status.replace(/_/g, ' ')}`}
          </span>
        )}
        <span style={{
          fontSize: 12, color: '#6b7280', flex: 1,
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }} title={run.file_path || ''}>
          {run.file_path || run.file_name || '(no file matched)'}
        </span>
        <Link to={`/reports/${run.run_id}`}
          style={{ fontSize: 11, color: '#6366f1', flexShrink: 0, fontWeight: 600 }}>
          Full report →
        </Link>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 80px)', gap: 8,
        marginBottom: (run.status_notes || run.error_detail) ? 10 : 0 }}>
        {[['Found', run.records_found], ['Loaded', run.records_loaded], ['Skipped', run.records_skipped]].map(([label, val]) => (
          <div key={label} style={{
            textAlign: 'center', background: '#f9fafb', borderRadius: 6,
            padding: '6px 4px', border: '1px solid #e5e7eb',
          }}>
            <div style={{
              fontSize: 20, fontWeight: 700,
              color: label === 'Skipped' && val > 0 ? '#92400e'
                   : label === 'Loaded'  && val > 0  ? '#166534'
                   : '#111827',
            }}>
              {val ?? '—'}
            </div>
            <div style={{ fontSize: 10, color: '#9ca3af', textTransform: 'uppercase', letterSpacing: '.06em' }}>
              {label}
            </div>
          </div>
        ))}
      </div>

      {run.status_notes && (
        <div style={{ fontSize: 11, color: '#6b7280', fontStyle: 'italic',
          marginBottom: run.error_detail ? 6 : 0 }}>
          {run.status_notes}
        </div>
      )}
      {run.error_detail && (
        <pre style={{
          fontSize: 10, color: '#991b1b', background: '#fff8f8',
          borderRadius: 4, padding: '6px 8px', overflow: 'auto', maxHeight: 140, margin: 0,
        }}>
          {run.error_detail}
        </pre>
      )}
    </div>
  )
}

function DatasetCard({ dataset, runState = {}, onIngest }) {
  const { status, runs = [], liveLog, error } = runState
  const isRunning = status === 'running'
  const isDone    = status === 'done'
  const isError   = status === 'error'

  const successCount = runs.filter(r => r.run_status === 'success').length
  const failedCount  = runs.filter(r => r.run_status === 'failed').length
  const skippedCount = runs.filter(r => r.run_status === 'skipped').length

  return (
    <div className="card" style={{ marginBottom: 12 }}>
      {/* Dataset header */}
      <div style={{ padding: '14px 16px', display: 'flex', alignItems: 'flex-start', gap: 12 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 700, fontSize: 14, color: '#111827', marginBottom: 4 }}>
            {dataset.dataset_name}
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
            {dataset.data_format && (
              <span className={`badge ${FORMAT_BADGE[dataset.data_format] || 'badge-gray'}`}>
                {dataset.data_format.toUpperCase()}
              </span>
            )}
            <span className="badge badge-blue">v{dataset.version}</span>
            {dataset.target_table && (
              <code style={{
                fontSize: 11, color: '#6b7280', background: '#f3f4f6',
                padding: '1px 6px', borderRadius: 4,
              }}>
                {dataset.target_table}
              </code>
            )}
          </div>
          {dataset.landing_folder && (
            <div style={{
              fontSize: 11, color: '#9ca3af', marginTop: 4,
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            }}>
              📁 {dataset.landing_folder}
              {dataset.file_name_pattern && (
                <span style={{ color: '#6b7280' }}> / {dataset.file_name_pattern}</span>
              )}
            </div>
          )}
          {dataset.dataset_desc && (
            <div style={{ fontSize: 12, color: '#6b7280', marginTop: 3 }}>{dataset.dataset_desc}</div>
          )}
        </div>
        <button
          className="btn btn-primary btn-sm"
          style={{ flexShrink: 0 }}
          disabled={isRunning}
          aria-busy={isRunning}
          onClick={() => onIngest(dataset.dataset_id)}
        >
          {isRunning
            ? <><span className="spinner" style={{ width: 12, height: 12 }} aria-hidden="true" /> Running…</>
            : isDone ? '↺ Re-run' : '▶ Run Ingestion'}
        </button>
      </div>

      {/* Progress / result panel */}
      {(isRunning || isDone || isError) && (
        <div style={{ borderTop: '1px solid #f3f4f6', padding: '12px 16px', background: '#fafafa' }}>

          {isRunning && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#1e40af', fontSize: 13, fontWeight: 500 }}>
                <span className="spinner" style={{ width: 16, height: 16 }} aria-hidden="true" />
                Scanning landing folder and processing files…
              </div>
              {liveLog && (
                <div style={{
                  fontSize: 12, color: '#374151', background: '#fff',
                  border: '1px solid #dbeafe', borderRadius: 6, padding: '8px 12px',
                  display: 'flex', alignItems: 'center', gap: 8,
                }}>
                  <StatusBadge status="in_progress" />
                  <span style={{ color: '#6b7280' }}>Run #{liveLog.run_id}</span>
                  {liveLog.file_name && <span>· {liveLog.file_name}</span>}
                </div>
              )}
            </div>
          )}

          {isError && (
            <div className="alert alert-error" style={{ margin: 0 }}>
              ✗ {error || 'Ingestion request failed'}
            </div>
          )}

          {isDone && (
            <>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 11, fontWeight: 700, color: '#6b7280', textTransform: 'uppercase', letterSpacing: '.06em' }}>
                  {runs.length} run{runs.length !== 1 ? 's' : ''} completed
                </span>
                {runs.length > 0 && (
                  <span style={{ fontSize: 11, color: '#9ca3af' }}>
                    {successCount > 0 && <span style={{ color: '#166534', marginRight: 6 }}>✓ {successCount} success</span>}
                    {failedCount  > 0 && <span style={{ color: '#991b1b', marginRight: 6 }}>✗ {failedCount} failed</span>}
                    {skippedCount > 0 && <span style={{ color: '#6b7280' }}>— {skippedCount} skipped</span>}
                  </span>
                )}
              </div>
              {runs.length === 0
                ? <div style={{ color: '#9ca3af', fontSize: 13, fontStyle: 'italic' }}>No run log entries returned.</div>
                : runs.map(r => <RunResult key={r.run_id} run={r} />)
              }
            </>
          )}
        </div>
      )}
    </div>
  )
}

export default function IngestionConsole() {
  const [providers, setProviders]       = useState([])
  const [selectedProv, setSelectedProv] = useState(null)
  const [datasets, setDatasets]         = useState([])
  const [loadingProv, setLoadingProv]   = useState(true)
  const [loadingDS, setLoadingDS]       = useState(false)
  const [runState, setRunState]         = useState({})
  const pollRefs = useRef({})

  useEffect(() => {
    return () => { Object.values(pollRefs.current).forEach(clearInterval) }
  }, [])

  const loadDatasets = useCallback(async (prov) => {
    setSelectedProv(prov)
    setDatasets([])
    setLoadingDS(true)
    try {
      const { data } = await getDatasets('', prov.provider_id)
      setDatasets(data)
    } catch {}
    finally { setLoadingDS(false) }
  }, [])

  useEffect(() => {
    getProviders()
      .then(({ data }) => {
        setProviders(data)
        if (data.length > 0) loadDatasets(data[0])
      })
      .catch(() => {})
      .finally(() => setLoadingProv(false))
  }, [loadDatasets])

  const handleIngest = async (dataset_id) => {
    // Clear any previous result and mark as running
    setRunState(s => ({ ...s, [dataset_id]: { status: 'running', runs: [], liveLog: null } }))

    // Poll for the in_progress log stub while the POST is in-flight
    pollRefs.current[dataset_id] = setInterval(async () => {
      try {
        const { data } = await getReports({ dataset_id, status: 'in_progress' })
        if (data.length > 0) {
          setRunState(s => ({
            ...s,
            [dataset_id]: { ...s[dataset_id], liveLog: data[0] },
          }))
        }
      } catch {}
    }, 2000)

    try {
      const { data } = await triggerIngest(dataset_id)
      clearInterval(pollRefs.current[dataset_id])
      setRunState(s => ({
        ...s,
        [dataset_id]: {
          status: 'done',
          runs: Array.isArray(data) ? data : [data],
          liveLog: null,
        },
      }))
    } catch (err) {
      clearInterval(pollRefs.current[dataset_id])
      setRunState(s => ({
        ...s,
        [dataset_id]: {
          status: 'error', runs: [], liveLog: null,
          error: err.response?.data?.error || err.message,
        },
      }))
    }
  }

  return (
    <>
      <div className="page-header" style={{ maxWidth: 1200 }}>
        <div>
          <div className="page-title">Ingestion Console</div>
          <div className="page-sub">
            Select a provider, trigger ingestion runs, and monitor results inline
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start', maxWidth: 1200 }}>

        {/* ── Provider panel ─────────────────────────────────────────── */}
        <div style={{ width: 240, flexShrink: 0 }}>
          <div className="card">
            <div className="card-header">
              <span>Providers</span>
              {!loadingProv && <span className="badge badge-gray">{providers.length}</span>}
            </div>
            {loadingProv ? (
              <div style={{ padding: 24, textAlign: 'center' }}>
                <span className="spinner" aria-label="Loading providers" />
              </div>
            ) : providers.length === 0 ? (
              <div style={{ padding: 16, color: '#9ca3af', fontSize: 13 }}>No providers found.</div>
            ) : (
              providers.map(p => (
                <button
                  key={p.provider_id}
                  onClick={() => loadDatasets(p)}
                  style={{
                    width: '100%', display: 'block', textAlign: 'left',
                    padding: '11px 14px', cursor: 'pointer', fontFamily: 'inherit',
                    borderTop: 'none', borderRight: 'none',
                    borderBottom: '1px solid #f3f4f6',
                    borderLeft: `3px solid ${selectedProv?.provider_id === p.provider_id ? '#6366f1' : 'transparent'}`,
                    background: selectedProv?.provider_id === p.provider_id ? '#eef2ff' : 'transparent',
                    transition: 'background .12s',
                  }}
                >
                  <div style={{
                    fontWeight: 600, fontSize: 13,
                    color: selectedProv?.provider_id === p.provider_id ? '#4f46e5' : '#374151',
                  }}>
                    {p.provider_name}
                  </div>
                  {p.provider_website && (
                    <div style={{
                      fontSize: 10, color: '#9ca3af', marginTop: 1,
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    }}>
                      {p.provider_website}
                    </div>
                  )}
                </button>
              ))
            )}
          </div>
        </div>

        {/* ── Dataset + run panel ────────────────────────────────────── */}
        <div style={{ flex: 1, minWidth: 0 }}>
          {!selectedProv ? (
            <div className="empty-state">
              <div className="empty-icon">◁</div>
              <p>Select a provider from the left to view its datasets</p>
            </div>
          ) : loadingDS ? (
            <div style={{ padding: 40, textAlign: 'center' }}>
              <span className="spinner" aria-label="Loading datasets" />
            </div>
          ) : (
            <>
              <div style={{ marginBottom: 14, display: 'flex', alignItems: 'center', gap: 10 }}>
                <span style={{ fontWeight: 700, fontSize: 15, color: '#111827' }}>
                  {selectedProv.provider_name}
                </span>
                <span className="badge badge-indigo">
                  {datasets.length} dataset{datasets.length !== 1 ? 's' : ''}
                </span>
              </div>

              {datasets.length === 0 ? (
                <div className="empty-state">
                  <div className="empty-icon">📦</div>
                  <p>No datasets registered for this provider.</p>
                </div>
              ) : (
                datasets.map(d => (
                  <DatasetCard
                    key={d.dataset_id}
                    dataset={d}
                    runState={runState[d.dataset_id]}
                    onIngest={handleIngest}
                  />
                ))
              )}
            </>
          )}
        </div>
      </div>
    </>
  )
}
