import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { getTransforms, saveTransforms } from '../../api'

const TRIM_OPTIONS = [
  { value: '',      label: '— None —'      },
  { value: 'trim',  label: 'Trim (both)'   },
  { value: 'ltrim', label: 'L-Trim (left)' },
  { value: 'rtrim', label: 'R-Trim (right)'},
]

const CASE_OPTIONS = [
  { value: '',         label: '— None —'      },
  { value: 'upper',    label: 'UPPER CASE'    },
  { value: 'lower',    label: 'lower case'    },
  { value: 'sentence', label: 'Sentence case' },
]

const DATE_FORMATS = [
  { value: '',                    label: '— Select —'                    },
  { value: '%Y-%m-%d',            label: 'YYYY-MM-DD'                    },
  { value: '%d/%m/%Y',            label: 'DD/MM/YYYY'                    },
  { value: '%m/%d/%Y',            label: 'MM/DD/YYYY'                    },
  { value: '%d-%m-%Y',            label: 'DD-MM-YYYY'                    },
  { value: '%Y%m%d',              label: 'YYYYMMDD'                      },
  { value: '%d %b %Y',            label: 'DD Mon YYYY  (15 Jan 2024)'    },
  { value: '%d %B %Y',            label: 'DD Month YYYY  (15 January 2024)'},
  { value: '%Y/%m/%d',            label: 'YYYY/MM/DD'                    },
  { value: '%m-%d-%Y',            label: 'MM-DD-YYYY'                    },
  { value: '%Y-%m-%d %H:%M:%S',   label: 'YYYY-MM-DD HH:MM:SS'          },
  { value: '%d/%m/%Y %H:%M:%S',   label: 'DD/MM/YYYY HH:MM:SS'          },
  { value: '%m/%d/%Y %H:%M:%S',   label: 'MM/DD/YYYY HH:MM:SS'          },
  { value: '%Y-%m-%dT%H:%M:%S',   label: 'ISO 8601  (YYYY-MM-DDTHH:MM:SS)'},
]

const TYPE_BADGE = {
  integer:  'badge-blue',
  decimal:  'badge-green',
  string:   'badge-gray',
  datetime: 'badge-amber',
  date:     'badge-amber',
  boolean:  'badge-indigo',
}

// Column types that support text-based operations (trim, case, date format).
const TEXT_TYPES = new Set(['string', 'date', 'datetime'])

export default function DatasetTransforms() {
  const { id } = useParams()
  const navigate = useNavigate()

  const [dataset, setDataset] = useState(null)
  const [rows, setRows]       = useState([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving]   = useState(false)
  const [apiError, setApiError] = useState('')
  const [saved, setSaved]     = useState(false)

  useEffect(() => {
    setLoading(true)
    getTransforms(id)
      .then(({ data }) => {
        setDataset(data.dataset)
        setRows(data.transforms.map(t => ({
          col_name:     t.col_name,
          col_type:     t.col_type || 'string',
          trim_option:  t.trim_option  || '',
          null_replace: t.null_replace ?? '',
          case_option:  t.case_option  || '',
          date_fmt_from: t.date_fmt_from || '',
          date_fmt_to:   t.date_fmt_to   || '',
        })))
      })
      .catch(() => setApiError('Failed to load transforms'))
      .finally(() => setLoading(false))
  }, [id])

  const setCell = (idx, field, val) => {
    setRows(prev => prev.map((r, i) => i === idx ? { ...r, [field]: val } : r))
    setSaved(false)
  }

  const handleSave = async () => {
    setSaving(true); setApiError(''); setSaved(false)
    try {
      await saveTransforms(id, rows)
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
    } catch (err) {
      setApiError(err.response?.data?.error || 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return <div style={{ padding: 40, textAlign: 'center' }}><span className="spinner" aria-label="Loading" /></div>
  }

  return (
    <>
      <div className="page-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <button type="button" className="btn btn-ghost btn-sm"
            onClick={() => navigate('/datasets')}>
            &#8592; Back to Datasets
          </button>
          <div>
            <div className="page-title">Column Transformations</div>
            <div className="page-sub">
              {dataset
                ? `${dataset.dataset_name}${dataset.provider_name ? ' — ' + dataset.provider_name : ''}`
                : ''}
            </div>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {saved && <span className="badge badge-green">&#10003; Saved</span>}
          <button className="btn btn-primary" onClick={handleSave}
            disabled={saving} aria-busy={saving}>
            {saving
              ? <><span className="spinner" style={{ width: 14, height: 14 }} aria-hidden="true" /> Saving…</>
              : 'Save Transforms'}
          </button>
        </div>
      </div>

      {apiError && <div className="alert alert-error" role="alert">{apiError}</div>}

      {rows.length === 0 ? (
        <div className="empty-state">
          <div className="empty-icon">&#128295;</div>
          <p>No columns found. Register columns via the Dataset form first.</p>
        </div>
      ) : (
        <div className="card" style={{ overflow: 'hidden' }}>
          <div className="card-header">
            <span>
              {rows.length} column{rows.length !== 1 ? 's' : ''}
            </span>
            <span style={{ fontSize: 11, color: '#9ca3af', fontWeight: 400 }}>
              Leave a dropdown on "— None —" to skip that transformation for the column.
              Transformations apply in order: Trim &#8594; Null Replace &#8594; Case &#8594; Date Format.
            </span>
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ tableLayout: 'fixed', minWidth: 900 }}>
              <colgroup>
                <col style={{ width: 160 }} />
                <col style={{ width: 90  }} />
                <col style={{ width: 140 }} />
                <col style={{ width: 170 }} />
                <col style={{ width: 150 }} />
                <col style={{ width: 180 }} />
                <col style={{ width: 180 }} />
              </colgroup>
              <thead>
                <tr>
                  <th>Column Name</th>
                  <th>Type</th>
                  <th>Trim</th>
                  <th>Replace Null</th>
                  <th>Case</th>
                  <th>Date Format — From</th>
                  <th>Date Format — To</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, idx) => {
                  const isText = TEXT_TYPES.has(row.col_type)
                  const disabledStyle = { opacity: 0.4, pointerEvents: 'none' }
                  return (
                    <tr key={row.col_name}>
                      <td>
                        <code style={{ fontSize: 12 }}>{row.col_name}</code>
                      </td>
                      <td>
                        <span className={`badge ${TYPE_BADGE[row.col_type] || 'badge-gray'}`}>
                          {row.col_type}
                        </span>
                      </td>

                      {/* Trim */}
                      <td style={!isText ? disabledStyle : {}}>
                        <select className="f-select" style={{ fontSize: 12, width: '100%' }}
                          value={row.trim_option}
                          disabled={!isText}
                          onChange={e => setCell(idx, 'trim_option', e.target.value)}>
                          {TRIM_OPTIONS.map(o => (
                            <option key={o.value} value={o.value}>{o.label}</option>
                          ))}
                        </select>
                      </td>

                      {/* Replace Null */}
                      <td>
                        <input className="f-input" style={{ fontSize: 12, width: '100%' }}
                          value={row.null_replace}
                          placeholder="leave null"
                          title="Replacement value for NULLs — leave blank to skip"
                          onChange={e => setCell(idx, 'null_replace', e.target.value)} />
                      </td>

                      {/* Case */}
                      <td style={!isText ? disabledStyle : {}}>
                        <select className="f-select" style={{ fontSize: 12, width: '100%' }}
                          value={row.case_option}
                          disabled={!isText}
                          onChange={e => setCell(idx, 'case_option', e.target.value)}>
                          {CASE_OPTIONS.map(o => (
                            <option key={o.value} value={o.value}>{o.label}</option>
                          ))}
                        </select>
                      </td>

                      {/* Date Format From */}
                      <td style={!isText ? disabledStyle : {}}>
                        <select className="f-select" style={{ fontSize: 12, width: '100%' }}
                          value={row.date_fmt_from}
                          disabled={!isText}
                          onChange={e => setCell(idx, 'date_fmt_from', e.target.value)}>
                          {DATE_FORMATS.map(o => (
                            <option key={o.value} value={o.value}>{o.label}</option>
                          ))}
                        </select>
                      </td>

                      {/* Date Format To */}
                      <td style={!isText ? disabledStyle : {}}>
                        <select className="f-select" style={{ fontSize: 12, width: '100%' }}
                          value={row.date_fmt_to}
                          disabled={!isText}
                          onChange={e => setCell(idx, 'date_fmt_to', e.target.value)}>
                          {DATE_FORMATS.map(o => (
                            <option key={o.value} value={o.value}>{o.label}</option>
                          ))}
                        </select>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  )
}
