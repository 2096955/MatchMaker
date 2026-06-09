import { useState, useEffect, useRef } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { getDataset, addDataset, editDataset, getProviders, uploadFile } from '../../api'

const FORMATS = ['csv', 'txt', 'xlsx', 'json', 'xml', 'parquet']
const FREQS   = ['Daily', 'Weekly', 'Monthly', 'Quarterly', 'Annually', 'On-demand', 'Real-time']
const TYPE_BADGE = { integer: 'badge-blue', decimal: 'badge-green', string: 'badge-gray', datetime: 'badge-amber', boolean: 'badge-indigo' }

// Maps data_format to the file extensions the browser file picker will accept.
const FORMAT_ACCEPT = {
  csv:     '.csv',
  txt:     '.txt',
  xlsx:    '.xlsx,.xls',
  json:    '.json',
  xml:     '.xml',
  parquet: '.parquet',
}

const EMPTY = {
  provider_id: '', dataset_name: '', dataset_desc: '', frequency: '',
  data_format: 'csv', file_name_pattern: '', landing_folder: '',
  delimiter: '', sheet_name: '',
}

export default function DatasetForm() {
  const { id } = useParams()
  const isEdit = Boolean(id)
  const navigate = useNavigate()
  const fileRef = useRef()

  const [form, setForm]             = useState(EMPTY)
  const [providers, setProviders]   = useState([])
  const [columns, setColumns]       = useState([])
  const [sampleRows, setSampleRows] = useState([])
  const [uploadedFile, setUploadedFile] = useState(null)
  const [discovering, setDiscovering]   = useState(false)
  const [errors, setErrors]         = useState({})
  const [apiError, setApiError]     = useState('')
  const [loading, setLoading]       = useState(false)
  const [saving, setSaving]         = useState(false)
  const [dragging, setDragging]     = useState(false)
  const [colsOpen, setColsOpen]     = useState(true)
  const [newColNames, setNewColNames] = useState(new Set())

  useEffect(() => {
    getProviders().then(({ data }) => setProviders(data)).catch(() => {})
    if (!isEdit) return
    setLoading(true)
    getDataset(id)
      .then(({ data }) => {
        setForm({
          provider_id:       data.provider_id || '',
          dataset_name:      data.dataset_name || '',
          dataset_desc:      data.dataset_desc || '',
          frequency:         data.frequency || '',
          data_format:       data.data_format || 'csv',
          file_name_pattern: data.file_name_pattern || '',
          landing_folder:    data.landing_folder || '',
          delimiter:         data.delimiter || '',
          sheet_name:        data.sheet_name || '',
        })
        setColumns(data.columns || [])
      })
      .catch(() => setApiError('Failed to load dataset'))
      .finally(() => setLoading(false))
  }, [id, isEdit])

  const set = (field, val) => {
    setForm(f => ({ ...f, [field]: val }))
    setErrors(e => ({ ...e, [field]: '' }))
  }

  const handleFileDrop = file => {
    if (!file) return
    const ext = file.name.split('.').pop().toLowerCase()
    const fileFmt = ext === 'xls' ? 'xlsx' : ext
    if (fileFmt !== form.data_format) {
      setApiError(
        `File ".${ext}" does not match the selected format "${form.data_format.toUpperCase()}". ` +
        `Please choose a ${form.data_format.toUpperCase()} file.`
      )
      return
    }
    setApiError('')
    setUploadedFile(file)
    setSampleRows([])
  }

  const handleDiscover = async () => {
    if (!uploadedFile) return
    setDiscovering(true); setApiError('')
    try {
      const { data } = await uploadFile(uploadedFile, form.data_format)
      if (isEdit && columns.length > 0) {
        const existingNames = new Set(columns.map(c => c.col_name.toLowerCase()))
        const newCols = data.columns.filter(c => !existingNames.has(c.col_name.toLowerCase()))
        setNewColNames(new Set(newCols.map(c => c.col_name.toLowerCase())))
        setColumns(prev => [...prev, ...newCols])
      } else {
        setNewColNames(new Set())
        setColumns(data.columns)
      }
      setSampleRows(data.sample_rows)
      setErrors(e => ({ ...e, columns: '' }))
    } catch (err) {
      setApiError(err.response?.data?.error || 'Failed to parse file')
    } finally {
      setDiscovering(false)
    }
  }

  const validate = () => {
    const e = {}
    if (!form.provider_id)              e.provider_id       = 'Provider is required'
    if (!form.dataset_name.trim())      e.dataset_name      = 'Dataset name is required'
    if (!form.data_format)              e.data_format       = 'Data format is required'
    if (!form.file_name_pattern.trim()) e.file_name_pattern = 'File name pattern is required'
    if (!form.landing_folder.trim())    e.landing_folder    = 'Landing folder is required'
    if (columns.length === 0)           e.columns           = 'Upload a file and run Discover Data before saving'
    setErrors(e)
    return Object.keys(e).length === 0
  }

  const handleSubmit = async ev => {
    ev.preventDefault()
    if (!validate()) return
    setSaving(true); setApiError('')
    try {
      const payload = { ...form, columns }
      if (isEdit) await editDataset(id, payload)
      else        await addDataset(payload)
      navigate('/datasets')
    } catch (err) {
      setApiError(err.response?.data?.error || 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div style={{ padding: 40, textAlign: 'center' }}><span className="spinner" aria-label="Loading" /></div>

  return (
    <>
      <div className="page-header" style={{ maxWidth: 946 }}>
        <div>
          <div className="page-title">{isEdit ? 'Edit Dataset' : 'Add Dataset'}</div>
          <div className="page-sub">
            {isEdit ? 'A new version will be created. Existing columns are preserved.' : 'Register a new third-party dataset'}
          </div>
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
          <button type="button" className="btn btn-ghost" onClick={() => navigate('/datasets')}>Cancel</button>
          <button type="submit" form="dataset-form" className="btn btn-primary" disabled={saving} aria-busy={saving}>
            {saving ? <><span className="spinner" style={{ width: 14, height: 14 }} aria-hidden="true" /> Saving…</> : isEdit ? 'Save Changes' : 'Register Dataset'}
          </button>
        </div>
      </div>

      {apiError && <div className="alert alert-error" role="alert">{apiError}</div>}

      <form id="dataset-form" onSubmit={handleSubmit} className="form-wrap" style={{ maxWidth: 946 }} noValidate aria-label={isEdit ? 'Edit dataset form' : 'Add dataset form'}>
        <div className="form-section">
          <div className="form-section-title">Provider &amp; Dataset Info</div>
          <div className="form-grid">

            <div className="form-group">
              <label className="form-label" htmlFor="f-provider-id">
                Provider <span aria-hidden="true">*</span><span className="sr-only"> (required)</span>
              </label>
              <select id="f-provider-id" className="f-select"
                required aria-required="true"
                aria-invalid={errors.provider_id ? 'true' : undefined}
                aria-describedby={errors.provider_id ? 'e-provider-id' : undefined}
                value={form.provider_id}
                onChange={e => set('provider_id', e.target.value)}>
                <option value="">— Select Provider —</option>
                {providers.map(p => (
                  <option key={p.provider_id} value={p.provider_id}>{p.provider_name}</option>
                ))}
              </select>
              {errors.provider_id && <span id="e-provider-id" className="form-error" role="alert">{errors.provider_id}</span>}
            </div>

            <div className="form-group">
              <label className="form-label" htmlFor="f-dataset-name">
                Dataset Name <span aria-hidden="true">*</span><span className="sr-only"> (required)</span>
              </label>
              <input id="f-dataset-name" className="f-input"
                required aria-required="true"
                aria-invalid={errors.dataset_name ? 'true' : undefined}
                aria-describedby={errors.dataset_name ? 'e-dataset-name' : undefined}
                value={form.dataset_name}
                onChange={e => set('dataset_name', e.target.value)}
                placeholder="e.g. equity_prices_daily" />
              {errors.dataset_name && <span id="e-dataset-name" className="form-error" role="alert">{errors.dataset_name}</span>}
            </div>

            <div className="form-group form-col-2">
              <label className="form-label" htmlFor="f-dataset-desc">Description</label>
              <textarea id="f-dataset-desc" className="f-textarea" rows={3}
                value={form.dataset_desc}
                onChange={e => set('dataset_desc', e.target.value)}
                placeholder="Describe what this dataset contains" />
            </div>

            <div className="form-group">
              <label className="form-label" htmlFor="f-frequency">Frequency</label>
              <select id="f-frequency" className="f-select"
                value={form.frequency}
                onChange={e => set('frequency', e.target.value)}>
                <option value="">— Select —</option>
                {FREQS.map(f => <option key={f} value={f}>{f}</option>)}
              </select>
            </div>

            <div className="form-group">
              <label className="form-label" htmlFor="f-data-format">
                Data Format <span aria-hidden="true">*</span><span className="sr-only"> (required)</span>
              </label>
              <select id="f-data-format" className="f-select"
                required aria-required="true"
                aria-invalid={errors.data_format ? 'true' : undefined}
                aria-describedby={errors.data_format ? 'e-data-format' : undefined}
                value={form.data_format}
                onChange={e => {
                  set('data_format', e.target.value)
                  // Clear any uploaded file and discovery results — they were for the old format.
                  setUploadedFile(null)
                  setSampleRows([])
                  setNewColNames(new Set())
                  if (!isEdit) setColumns([])
                  if (fileRef.current) fileRef.current.value = ''
                }}>
                {FORMATS.map(f => <option key={f} value={f}>{f.toUpperCase()}</option>)}
              </select>
              {errors.data_format && <span id="e-data-format" className="form-error" role="alert">{errors.data_format}</span>}
            </div>

            {form.data_format === 'txt' && (
              <div className="form-group">
                <label className="form-label" htmlFor="f-delimiter">Delimiter</label>
                <input id="f-delimiter" className="f-input"
                  value={form.delimiter}
                  onChange={e => set('delimiter', e.target.value)}
                  placeholder="e.g. , or | or \t" />
              </div>
            )}
            {form.data_format === 'xlsx' && (
              <div className="form-group">
                <label className="form-label" htmlFor="f-sheet-name">Sheet Name</label>
                <input id="f-sheet-name" className="f-input"
                  value={form.sheet_name}
                  onChange={e => set('sheet_name', e.target.value)}
                  placeholder="e.g. Fundamentals (blank = first sheet)" />
              </div>
            )}

            <div className="form-group">
              <label className="form-label" htmlFor="f-file-pattern">
                File Name Pattern <span aria-hidden="true">*</span><span className="sr-only"> (required)</span>
              </label>
              <input id="f-file-pattern" className="f-input"
                required aria-required="true"
                aria-invalid={errors.file_name_pattern ? 'true' : undefined}
                aria-describedby={errors.file_name_pattern ? 'e-file-pattern' : undefined}
                value={form.file_name_pattern}
                onChange={e => set('file_name_pattern', e.target.value)}
                placeholder="e.g. equity_prices_*.csv" />
              {errors.file_name_pattern && <span id="e-file-pattern" className="form-error" role="alert">{errors.file_name_pattern}</span>}
            </div>

            <div className="form-group">
              <label className="form-label" htmlFor="f-landing-folder">
                Landing Folder <span aria-hidden="true">*</span><span className="sr-only"> (required)</span>
              </label>
              <input id="f-landing-folder" className="f-input"
                required aria-required="true"
                aria-invalid={errors.landing_folder ? 'true' : undefined}
                aria-describedby={errors.landing_folder ? 'e-landing-folder' : undefined}
                value={form.landing_folder}
                onChange={e => set('landing_folder', e.target.value)}
                placeholder="e.g. /data/landing/equity/" />
              {errors.landing_folder && <span id="e-landing-folder" className="form-error" role="alert">{errors.landing_folder}</span>}
            </div>

          </div>
        </div>

        <div className="form-section">
          <div className="form-section-title">Sample File &amp; Data Discovery</div>
          <div style={{ padding: '10px 20px', display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div
              role="button"
              tabIndex={0}
              aria-label={uploadedFile
                ? `Selected: ${uploadedFile.name}. Press Enter or Space to change file`
                : 'Upload sample file. Press Enter or Space to browse, or drag a file here'}
              className={`upload-zone${dragging ? ' dragging' : ''}`}
              style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 16px', textAlign: 'left' }}
              onClick={() => fileRef.current.click()}
              onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileRef.current.click() } }}
              onDragOver={e => { e.preventDefault(); setDragging(true) }}
              onDragLeave={() => setDragging(false)}
              onDrop={e => { e.preventDefault(); setDragging(false); handleFileDrop(e.dataTransfer.files[0]) }}
            >
              <input type="file" ref={fileRef} aria-hidden="true" tabIndex={-1}
                accept={FORMAT_ACCEPT[form.data_format] || '*'}
                onChange={e => handleFileDrop(e.target.files[0])} />
              <span style={{ fontSize: 20, flexShrink: 0 }} aria-hidden="true">📂</span>
              <div>
                {uploadedFile
                  ? <p style={{ fontSize: 13 }}><strong>{uploadedFile.name}</strong> <span style={{ color: '#6b7280' }}>({(uploadedFile.size / 1024).toFixed(1)} KB)</span></p>
                  : <p style={{ fontSize: 13 }}>Click or drag a sample file here</p>}
                <small style={{ fontSize: 11, color: '#6b7280' }}>
                  Accepted: <strong>{form.data_format.toUpperCase()}</strong> only
                  {form.data_format === 'xlsx' ? ' (.xlsx, .xls)' : ` (.${form.data_format})`}
                </small>
              </div>
            </div>

            <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
              <button type="button" className="btn btn-primary btn-sm"
                disabled={!uploadedFile || discovering}
                aria-busy={discovering}
                onClick={handleDiscover}>
                {discovering
                  ? <><span className="spinner" style={{ width: 12, height: 12 }} aria-hidden="true" /> Discovering…</>
                  : '🔍 Discover Data'}
              </button>
              {sampleRows.length > 0 && (
                <span className="badge badge-green" aria-live="polite">✓ {sampleRows.length} sample rows loaded</span>
              )}
              {errors.columns && <span id="e-columns" className="form-error" role="alert">{errors.columns}</span>}
            </div>
          </div>
        </div>

        {columns.length > 0 && (
          <div className="form-section">
            <div
              className="form-section-title"
              role="button"
              tabIndex={0}
              aria-expanded={colsOpen}
              aria-controls="columns-panel"
              style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', cursor: 'pointer', userSelect: 'none' }}
              onClick={() => setColsOpen(v => !v)}
              onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setColsOpen(v => !v) } }}
            >
              <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                Discovered Columns
                <span style={{ fontSize: 12, fontWeight: 400, color: '#6b7280' }}>({columns.length})</span>
                {isEdit && newColNames.size > 0 && (
                  <span className="badge badge-green" style={{ fontSize: 10 }}>+ {newColNames.size} new</span>
                )}
              </span>
              <span style={{ fontSize: 11, color: '#6b7280' }} aria-hidden="true">{colsOpen ? '▲ Collapse' : '▼ Expand'}</span>
            </div>
            <div id="columns-panel" hidden={!colsOpen}>
              <div style={{ overflowX: 'auto' }}>
                <table className="col-table" aria-label="Discovered dataset columns">
                  <thead>
                    <tr>
                      <th scope="col">#</th>
                      <th scope="col">Column Name</th>
                      <th scope="col">Inferred Type</th>
                    </tr>
                  </thead>
                  <tbody>
                    {columns.map((col, i) => {
                      const isNew = isEdit && newColNames.has(col.col_name.toLowerCase())
                      return (
                        <tr key={i} style={isNew ? { background: '#f0fdf4', borderLeft: '3px solid #16a34a' } : { borderLeft: '3px solid transparent' }}>
                          <td style={{ color: '#6b7280', width: 40 }}>{col.col_position || i + 1}</td>
                          <td>
                            <code style={{ fontSize: 12 }}>{col.col_name}</code>
                            {isNew && (
                              <span className="badge badge-green" style={{ marginLeft: 8, fontSize: 10, verticalAlign: 'middle' }}>
                                <span aria-hidden="true">+</span> New
                              </span>
                            )}
                          </td>
                          <td>
                            <span className={`badge ${TYPE_BADGE[col.col_type] || 'badge-gray'}`}>
                              {col.col_type}
                            </span>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}

        {sampleRows.length > 0 && (
          <div className="form-section">
            <div className="form-section-title">Sample Data Preview (first 10 rows)</div>
            <div className="sample-preview">
              <table aria-label="Sample data preview">
                <thead>
                  <tr>{Object.keys(sampleRows[0]).map(k => <th key={k} scope="col">{k}</th>)}</tr>
                </thead>
                <tbody>
                  {sampleRows.map((row, i) => (
                    <tr key={i}>
                      {Object.values(row).map((v, j) => (
                        <td key={j} style={{ maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {String(v)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

      </form>
    </>
  )
}
