import { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'
import { getReport } from '../../api'

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

function InfoRow({ label, value }) {
  return (
    <div style={{ display: 'flex', gap: 8, padding: '6px 0', borderBottom: '1px solid #f3f4f6', alignItems: 'flex-start' }}>
      <span style={{ minWidth: 140, fontSize: 12, color: '#6b7280', flexShrink: 0 }}>{label}</span>
      <span style={{ fontSize: 13, wordBreak: 'break-all' }}>{value ?? '—'}</span>
    </div>
  )
}

function SectionCard({ title, children }) {
  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      <div style={{ padding: '10px 16px', borderBottom: '1px solid #e5e7eb', background: '#f9fafb' }}>
        <strong style={{ fontSize: 13 }}>{title}</strong>
      </div>
      <div style={{ padding: '10px 16px' }}>
        {children}
      </div>
    </div>
  )
}

function formatBytes(bytes) {
  if (bytes == null) return '—'
  return (bytes / 1024).toFixed(2) + ' KB'
}

export default function ReportDetail() {
  const { id } = useParams()
  const [report, setReport] = useState(null)
  const [loading, setLoading] = useState(true)
  const [notFound, setNotFound] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    setLoading(true)
    getReport(id)
      .then(({ data }) => setReport(data))
      .catch(err => {
        if (err.response?.status === 404) setNotFound(true)
        else setError('Failed to load report')
      })
      .finally(() => setLoading(false))
  }, [id])

  if (loading) {
    return <div style={{ padding: 40, textAlign: 'center' }}><span className="spinner" aria-label="Loading" /></div>
  }

  if (notFound) {
    return (
      <div style={{ maxWidth: '100%' }}>
        <div className="page-header">
          <div>
            <div className="page-title">Report Not Found</div>
            <div className="page-sub">Run #{id} does not exist</div>
          </div>
          <Link to="/reports" className="btn btn-ghost">&#8592; Back to Reports</Link>
        </div>
      </div>
    )
  }

  if (error) {
    return <div className="alert alert-error" style={{ maxWidth: '100%' }}>{error}</div>
  }

  if (!report) return null

  const showError = report.run_status === 'failed' || (report.error_detail && report.error_detail.trim())

  return (
    <>
      <div className="page-header" style={{ maxWidth: '100%' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <Link to="/reports" className="btn btn-ghost btn-sm">&#8592; Back to Reports</Link>
          <div>
            <div className="page-title">Run #{report.run_id}</div>
            <div className="page-sub">
              {report.dataset_name}
              {report.provider_name ? ` — ${report.provider_name}` : ''}
            </div>
          </div>
        </div>
      </div>

      {/* 2-column grid of summary cards */}
      <div style={{ maxWidth: '100%', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 16 }}>

        <SectionCard title="Run Summary">
          <InfoRow label="Run ID" value={report.run_id} />
          <InfoRow label="Status" value={<StatusBadge status={report.run_status} />} />
          <InfoRow label="Run Date" value={report.run_date ? new Date(report.run_date).toLocaleString() : null} />
          <InfoRow label="Created By" value={report.created_by} />
        </SectionCard>

        <SectionCard title="Dataset Info">
          <InfoRow label="Provider" value={report.provider_name} />
          <InfoRow label="Dataset" value={report.dataset_name} />
          <InfoRow label="Version" value={report.dataset_version != null ? `v${report.dataset_version}` : null} />
          <InfoRow label="Target Table" value={report.target_table} />
        </SectionCard>

        <SectionCard title="File Info">
          <InfoRow label="File Path"     value={report.file_path} />
          <InfoRow label="File Size"     value={formatBytes(report.file_size_bytes)} />
          <InfoRow label="File Modified" value={report.file_mtime ? new Date(report.file_mtime).toLocaleString() : null} />
        </SectionCard>

        <SectionCard title="Record Counts">
          <InfoRow label="Records Found"   value={report.records_found   != null ? report.records_found.toLocaleString()   : null} />
          <InfoRow label="Records Loaded"  value={report.records_loaded  != null ? report.records_loaded.toLocaleString()  : null} />
          <InfoRow label="Records Skipped" value={report.records_skipped != null ? report.records_skipped.toLocaleString() : null} />
        </SectionCard>

      </div>

      {/* Column Status — full width */}
      <div style={{ maxWidth: '100%', marginBottom: 16 }}>
        <SectionCard title="Column Status">
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
            {report.column_status && (
              <span className="badge badge-gray" style={{ flexShrink: 0 }}>{report.column_status}</span>
            )}
            {report.status_notes && (
              <span style={{ fontSize: 13, color: '#374151' }}>{report.status_notes}</span>
            )}
            {!report.column_status && !report.status_notes && (
              <span style={{ color: '#9ca3af', fontSize: 13 }}>No column status recorded.</span>
            )}
          </div>
        </SectionCard>
      </div>

      {/* Error Detail — only shown when relevant */}
      {showError && (
        <div style={{ maxWidth: '100%', marginBottom: 16 }}>
          <SectionCard title="Error Detail">
            {report.error_detail ? (
              <pre style={{
                fontFamily: 'monospace',
                fontSize: 12,
                background: '#fef2f2',
                border: '1px solid #fecaca',
                borderRadius: 4,
                padding: '10px 14px',
                overflowX: 'auto',
                maxHeight: 320,
                overflowY: 'auto',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
                color: '#991b1b',
                margin: 0,
              }}>
                {report.error_detail}
              </pre>
            ) : (
              <span style={{ color: '#9ca3af', fontSize: 13 }}>No error detail recorded.</span>
            )}
          </SectionCard>
        </div>
      )}
    </>
  )
}
