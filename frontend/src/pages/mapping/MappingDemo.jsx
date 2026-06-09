import { useCallback, useEffect, useRef, useState } from 'react'
import {
  describeMappingAgent,
  getMappingVendors,
  ingestMappingFile,
  listMappingWorkingSet,
  recordMappingDecision,
  runAgentStream,
} from '../../api'

// ──────────────────────────────────────────────────────────────────────
// SCUDO Mapping Demo — single page, three panels:
//   Files            Agent Activity            Matching + HITL
//   ─────            ──────────────            ────────────────
//   pick vendor      live stream of MCP        agent's final result
//   drop file        tool_call / tool_result   approve / override / reject
//   pick a product   pairs + agent_messages    candidates + validations
//
// One screen, one purpose: show the MCP being exercised by an agent
// against real vendor files, with the human closing the HITL loop.
// ──────────────────────────────────────────────────────────────────────

const PANEL = {
  display: 'flex',
  flexDirection: 'column',
  border: '1px solid #e5e7eb',
  borderRadius: 10,
  background: '#fff',
  overflow: 'hidden',
  minHeight: 0,
}

const PANEL_HEADER = {
  padding: '12px 16px',
  borderBottom: '1px solid #f3f4f6',
  background: '#f9fafb',
  fontSize: 12,
  fontWeight: 700,
  letterSpacing: '.06em',
  textTransform: 'uppercase',
  color: '#6b7280',
}

const PANEL_BODY = {
  flex: 1,
  overflowY: 'auto',
  padding: '12px 16px',
  minHeight: 0,
}

const STATUS_CFG = {
  auto_mapped:  { cls: 'badge-green',  label: 'AUTO MAPPED' },
  needs_review: { cls: 'badge-amber',  label: 'NEEDS REVIEW' },
  out_of_scope: { cls: 'badge-red',    label: 'OUT OF SCOPE' },
  approved:     { cls: 'badge-green',  label: 'APPROVED' },
  overridden:   { cls: 'badge-indigo', label: 'OVERRIDDEN' },
  rejected:     { cls: 'badge-red',    label: 'REJECTED' },
}

function StatusBadge({ status }) {
  const cfg = STATUS_CFG[status] || { cls: 'badge-gray', label: status }
  return <span className={`badge ${cfg.cls}`}>{cfg.label}</span>
}

function EventIcon({ type }) {
  const icon = {
    start:        '▶',
    tool_call:    '◇',
    tool_result:  '✓',
    agent_message:'✦',
    final_result: '■',
    error:        '⚠',
    done:         '·',
  }[type] || '•'
  const color = {
    start:        '#6366f1',
    tool_call:    '#0ea5e9',
    tool_result:  '#16a34a',
    agent_message:'#a855f7',
    final_result: '#1e293b',
    error:        '#dc2626',
    done:         '#9ca3af',
  }[type] || '#6b7280'
  return (
    <span style={{
      display: 'inline-block', width: 18, textAlign: 'center',
      color, fontWeight: 700, fontSize: 13, flexShrink: 0,
    }}>{icon}</span>
  )
}

function AgentEventRow({ event }) {
  const renderBody = () => {
    switch (event.type) {
      case 'start':
        return (
          <div>
            <strong>{event.vendor}</strong> · <code>{event.product_id}</code>
            <div style={{ fontSize: 11, color: '#9ca3af', marginTop: 2 }}>
              backend: {event.agent_backend}
              {event.model_id ? ` · ${event.model_id}` : ''}
            </div>
          </div>
        )
      case 'tool_call':
        return (
          <div>
            <code style={{ fontWeight: 600 }}>{event.tool}</code>
            <pre style={{
              margin: '4px 0 0', fontSize: 11, color: '#6b7280',
              background: '#f9fafb', borderRadius: 4, padding: '6px 8px',
              maxHeight: 80, overflow: 'auto', whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
            }}>{JSON.stringify(event.args, null, 2)}</pre>
          </div>
        )
      case 'tool_result': {
        const r = event.result
        let summary = ''
        if (r && typeof r === 'object') {
          if ('count' in r) summary = `${r.count} candidate${r.count === 1 ? '' : 's'}`
          else if ('label' in r) summary = `${r.label} (${r.iri})`
          else if ('status' in r) summary = `${r.status} · ${r.mapped_node_label || ''}`
          else if ('nodes' in r) summary = `subgraph: ${r.nodes?.length || 0} nodes`
        }
        return (
          <div>
            <code style={{ fontWeight: 600 }}>{event.tool}</code>
            <div style={{ fontSize: 12, color: '#374151', marginTop: 2 }}>
              {summary || (r ? '(result returned)' : '(no result)')}
            </div>
          </div>
        )
      }
      case 'agent_message':
        return (
          <div style={{ fontSize: 13, color: '#4f46e5', fontStyle: 'italic' }}>
            {event.content}
          </div>
        )
      case 'final_result':
        return (
          <div style={{ fontSize: 12, color: '#1e293b' }}>
            <strong>final_result</strong> — matcher status:{' '}
            <code>{event.mapping?.status}</code>
          </div>
        )
      case 'error':
        return (
          <div style={{ fontSize: 12, color: '#991b1b' }}>
            <strong>error</strong> — {event.error}
            {event.hint && (
              <div style={{ fontSize: 11, color: '#6b7280', marginTop: 2 }}>
                {event.hint}
              </div>
            )}
          </div>
        )
      case 'done':
        return (
          <div style={{ fontSize: 11, color: '#9ca3af' }}>agent run complete</div>
        )
      default:
        return <pre style={{ fontSize: 11 }}>{JSON.stringify(event, null, 2)}</pre>
    }
  }
  return (
    <div style={{
      display: 'flex', gap: 10, padding: '10px 0',
      borderBottom: '1px solid #f3f4f6',
    }}>
      <EventIcon type={event.type} />
      <div style={{ flex: 1, minWidth: 0 }}>{renderBody()}</div>
    </div>
  )
}

function CandidateRow({ candidate }) {
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between',
      padding: '6px 8px', borderBottom: '1px solid #f3f4f6',
      fontSize: 12,
    }}>
      <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>
        <strong>{candidate.node?.label || '—'}</strong>
        <div style={{ fontFamily: 'monospace', fontSize: 11, color: '#9ca3af' }}>
          {candidate.node?.iri}
        </div>
      </span>
      <span style={{
        minWidth: 50, textAlign: 'right',
        fontVariantNumeric: 'tabular-nums',
        color: candidate.similarity >= 0.80 ? '#166534' : '#92400e',
        fontWeight: 600,
      }}>
        {(candidate.similarity ?? 0).toFixed(2)}
      </span>
    </div>
  )
}

function ValidationRow({ v }) {
  const color = v.status === 'pass' ? '#166534'
              : v.status === 'fail' ? '#991b1b'
              : '#92400e'
  const icon = v.status === 'pass' ? '✓' : v.status === 'fail' ? '✗' : '⚠'
  return (
    <div style={{
      display: 'flex', gap: 8, padding: '4px 8px',
      fontSize: 12, color: '#374151',
    }}>
      <span style={{ color, fontWeight: 700, width: 14 }}>{icon}</span>
      <span style={{ flex: 1, minWidth: 0 }}>
        {v.name}{v.required ? '' : ' (warn)'}
        {v.detail && (
          <div style={{ fontSize: 11, color: '#9ca3af' }}>{v.detail}</div>
        )}
      </span>
    </div>
  )
}

export default function MappingDemo() {
  const [vendors, setVendors]           = useState([])
  const [vendor, setVendor]             = useState('')
  const [workingSet, setWorkingSet]     = useState([])
  const [selected, setSelected]         = useState(null)  // product_id
  const [agentBackend, setAgentBackend] = useState(null)
  const [events, setEvents]             = useState([])
  const [running, setRunning]           = useState(false)
  const [mapping, setMapping]           = useState(null)
  const [error, setError]               = useState('')
  const [decisionMsg, setDecisionMsg]   = useState('')
  const [overrideIri, setOverrideIri]   = useState('')
  const abortRunRef                     = useRef(null)
  const eventsBottomRef                 = useRef(null)
  const fileInputRef                    = useRef(null)

  // Load vendors + working set + agent backend description once.
  useEffect(() => {
    getMappingVendors()
      .then(({ data }) => {
        setVendors(data.vendors || [])
        if (data.default) setVendor(data.default)
      })
      .catch(() => setVendors([]))
    describeMappingAgent()
      .then(({ data }) => setAgentBackend(data))
      .catch(() => setAgentBackend({ backend: 'unknown' }))
    reloadWorkingSet()
  }, [])

  const reloadWorkingSet = useCallback(() => {
    listMappingWorkingSet()
      .then(({ data }) => setWorkingSet(data.products || []))
      .catch(() => setWorkingSet([]))
  }, [])

  // Auto-scroll the activity panel to the latest event.
  useEffect(() => {
    eventsBottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [events])

  const handleIngest = async (file) => {
    setError('')
    try {
      await ingestMappingFile(vendor, file)
      reloadWorkingSet()
    } catch (err) {
      setError(err.response?.data?.error || 'File ingest failed')
    }
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  const handleRunAgent = (product) => {
    setSelected(product.product_id)
    setEvents([])
    setMapping(null)
    setError('')
    setDecisionMsg('')
    setOverrideIri('')
    setRunning(true)
    const abort = runAgentStream(
      {
        vendor: product.vendor,
        productId: product.product_id,
        name: product.name,
        description: product.description,
      },
      (event) => {
        setEvents(prev => [...prev, event])
        if (event.type === 'final_result' && event.mapping) {
          setMapping(event.mapping)
        }
        if (event.type === 'error') {
          setError(event.error || 'Agent run failed')
        }
        if (event.type === 'done' || event.type === 'error') {
          setRunning(false)
        }
      },
    )
    abortRunRef.current = abort
  }

  const handleStop = () => {
    abortRunRef.current?.()
    setRunning(false)
  }

  const handleDecision = async (decision) => {
    if (!mapping) return
    setDecisionMsg('')
    const targetIri = decision === 'override'
      ? overrideIri.trim()
      : mapping.mapped_node_iri
    if (decision === 'override' && !targetIri) {
      setDecisionMsg('Override target IRI required')
      return
    }
    try {
      const body = {
        vendor: mapping.vendor,
        product_id: mapping.product_id,
        decision,
        node_iri: targetIri,
      }
      if (decision === 'approve') {
        body.suggested_confidence = mapping.confidence
      }
      const { data } = await recordMappingDecision(body)
      setMapping(data)
      setDecisionMsg(`Recorded: ${data.status.toUpperCase()}`)
    } catch (err) {
      setDecisionMsg(err.response?.data?.error || 'Decision failed')
    }
  }

  const onDrop = (e) => {
    e.preventDefault()
    const file = e.dataTransfer.files?.[0]
    if (file) handleIngest(file)
  }

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">SCUDO Mapping Demo</div>
          <div className="page-sub">
            Drop a vendor file · agent calls the MCP · human closes the loop
            {agentBackend && (
              <>
                {' · '}
                <span className={`badge ${agentBackend.backend === 'bedrock' ? 'badge-indigo' : 'badge-gray'}`}>
                  agent: {agentBackend.backend}
                  {agentBackend.model_id ? ` (${agentBackend.model_id})` : ''}
                </span>
              </>
            )}
          </div>
        </div>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      <div style={{
        display: 'grid',
        gridTemplateColumns: '300px 1fr 380px',
        gap: 16,
        height: 'calc(100vh - 220px)',
        minHeight: 480,
      }}>
        {/* ─── Files panel ─── */}
        <div style={PANEL}>
          <div style={PANEL_HEADER}>Files</div>
          <div style={PANEL_BODY}>
            <div style={{ marginBottom: 12 }}>
              <label style={{ fontSize: 12, color: '#6b7280', marginBottom: 4, display: 'block' }}>
                Vendor
              </label>
              <select
                value={vendor}
                onChange={e => setVendor(e.target.value)}
                className="f-select"
                style={{ width: '100%' }}
                disabled={running}
              >
                {vendors.map(v => <option key={v} value={v}>{v}</option>)}
              </select>
            </div>

            <div
              className="upload-zone"
              onDragOver={e => e.preventDefault()}
              onDrop={onDrop}
              onClick={() => fileInputRef.current?.click()}
              style={{ marginBottom: 16, padding: 16, fontSize: 12 }}
            >
              <div className="upload-icon">📥</div>
              <p>Drop a CSV/JSON</p>
              <small>columns: product_id, name, description</small>
              <input
                ref={fileInputRef}
                type="file"
                accept=".csv,.json,.tsv"
                onChange={e => e.target.files?.[0] && handleIngest(e.target.files[0])}
              />
            </div>

            <div style={{ fontSize: 11, fontWeight: 700, color: '#9ca3af',
                          textTransform: 'uppercase', letterSpacing: '.06em',
                          marginBottom: 6 }}>
              Working set ({workingSet.length})
            </div>

            {workingSet.length === 0 ? (
              <div style={{ fontSize: 12, color: '#9ca3af', padding: 12 }}>
                No products yet. Drop a vendor file to get started.
              </div>
            ) : (
              <div>
                {workingSet.map(p => (
                  <div
                    key={`${p.vendor}-${p.product_id}`}
                    style={{
                      padding: '8px 10px',
                      borderRadius: 6,
                      marginBottom: 4,
                      cursor: running ? 'not-allowed' : 'pointer',
                      background: selected === p.product_id ? '#eef2ff' : 'transparent',
                      border: selected === p.product_id ? '1px solid #c7d2fe' : '1px solid transparent',
                    }}
                    onClick={() => !running && handleRunAgent(p)}
                  >
                    <div style={{ fontSize: 12, fontWeight: 600, color: '#374151' }}>
                      {p.name || p.product_id}
                    </div>
                    <div style={{ fontSize: 11, color: '#9ca3af',
                                  fontFamily: 'monospace' }}>
                      {p.vendor} · {p.product_id}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* ─── Agent activity panel ─── */}
        <div style={PANEL}>
          <div style={{
            ...PANEL_HEADER,
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          }}>
            <span>Agent Activity</span>
            {running && (
              <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span className="spinner" style={{ width: 12, height: 12 }} />
                <button className="btn btn-ghost btn-sm" onClick={handleStop}>Stop</button>
              </span>
            )}
          </div>
          <div style={PANEL_BODY}>
            {events.length === 0 && !running ? (
              <div className="empty-state" style={{ padding: 30 }}>
                <div className="empty-icon">🤖</div>
                <p>Pick a product to map.</p>
                <small style={{ color: '#9ca3af' }}>
                  The agent will call the MCP tools live.
                </small>
              </div>
            ) : (
              <>
                {events.map((e, i) => <AgentEventRow key={i} event={e} />)}
                <div ref={eventsBottomRef} />
              </>
            )}
          </div>
        </div>

        {/* ─── Matching + HITL panel ─── */}
        <div style={PANEL}>
          <div style={PANEL_HEADER}>Matching + HITL</div>
          <div style={PANEL_BODY}>
            {!mapping ? (
              <div className="empty-state" style={{ padding: 30 }}>
                <div className="empty-icon">🎯</div>
                <p>No mapping yet.</p>
                <small style={{ color: '#9ca3af' }}>
                  Run the agent on a product to see the matcher's result.
                </small>
              </div>
            ) : (
              <>
                <div style={{ marginBottom: 12 }}>
                  <StatusBadge status={mapping.status} />
                </div>

                <div style={{ fontSize: 12, color: '#6b7280', marginBottom: 4 }}>
                  Vendor product
                </div>
                <div style={{ fontSize: 13, color: '#111827', marginBottom: 12 }}>
                  <strong>{mapping.product_name || mapping.product_id}</strong>
                  <div style={{ fontFamily: 'monospace', fontSize: 11,
                                color: '#9ca3af', wordBreak: 'break-all' }}>
                    {mapping.vendor} · {mapping.product_id}
                  </div>
                </div>

                {mapping.mapped_node_iri && (
                  <>
                    <div style={{ fontSize: 12, color: '#6b7280', marginBottom: 4 }}>
                      Mapped to
                    </div>
                    <div style={{ fontSize: 13, color: '#111827', marginBottom: 4 }}>
                      <strong>{mapping.mapped_node_label}</strong>
                    </div>
                    <div style={{ fontFamily: 'monospace', fontSize: 11,
                                  color: '#6b7280', marginBottom: 12,
                                  wordBreak: 'break-all' }}>
                      {mapping.mapped_node_iri}
                    </div>

                    <div style={{ display: 'flex', justifyContent: 'space-between',
                                  marginBottom: 12 }}>
                      <span style={{ fontSize: 12, color: '#6b7280' }}>
                        Confidence
                      </span>
                      <span style={{
                        fontSize: 13, fontWeight: 700,
                        color: mapping.confidence >= 0.80 ? '#166534' : '#92400e',
                        fontVariantNumeric: 'tabular-nums',
                      }}>
                        {(mapping.confidence ?? 0).toFixed(2)}
                      </span>
                    </div>
                  </>
                )}

                {mapping.rationale && (
                  <div style={{
                    fontSize: 11, color: '#6b7280', fontStyle: 'italic',
                    padding: '8px 10px', background: '#f9fafb',
                    borderRadius: 6, marginBottom: 12,
                  }}>
                    {mapping.rationale}
                  </div>
                )}

                {mapping.candidates?.length > 0 && (
                  <>
                    <div style={{ fontSize: 11, fontWeight: 700, color: '#9ca3af',
                                  textTransform: 'uppercase', letterSpacing: '.06em',
                                  marginBottom: 6 }}>
                      Candidates
                    </div>
                    <div style={{ marginBottom: 12,
                                  border: '1px solid #f3f4f6', borderRadius: 6 }}>
                      {mapping.candidates.slice(0, 5).map((c, i) => (
                        <CandidateRow key={i} candidate={c} />
                      ))}
                    </div>
                  </>
                )}

                {mapping.validations?.length > 0 && (
                  <>
                    <div style={{ fontSize: 11, fontWeight: 700, color: '#9ca3af',
                                  textTransform: 'uppercase', letterSpacing: '.06em',
                                  marginBottom: 6 }}>
                      Validations
                    </div>
                    <div style={{ marginBottom: 16,
                                  border: '1px solid #f3f4f6', borderRadius: 6 }}>
                      {mapping.validations.map((v, i) => (
                        <ValidationRow key={i} v={v} />
                      ))}
                    </div>
                  </>
                )}

                {/* HITL controls */}
                {(mapping.status === 'auto_mapped' ||
                  mapping.status === 'needs_review') && (
                  <div style={{
                    borderTop: '1px solid #e5e7eb', paddingTop: 12, marginTop: 8,
                  }}>
                    <div style={{ fontSize: 11, fontWeight: 700, color: '#9ca3af',
                                  textTransform: 'uppercase', letterSpacing: '.06em',
                                  marginBottom: 8 }}>
                      Human decision
                    </div>
                    <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
                      <button
                        className="btn btn-primary btn-sm"
                        onClick={() => handleDecision('approve')}
                        disabled={!mapping.mapped_node_iri}
                      >
                        Approve
                      </button>
                      <button
                        className="btn btn-ghost btn-sm"
                        onClick={() => handleDecision('reject')}
                        disabled={!mapping.mapped_node_iri}
                      >
                        Reject
                      </button>
                    </div>
                    <div style={{ display: 'flex', gap: 6 }}>
                      <input
                        className="f-input"
                        style={{ flex: 1, fontSize: 12, padding: '6px 8px',
                                 height: 30 }}
                        placeholder="Override IRI (e.g. cdao:fx)"
                        value={overrideIri}
                        onChange={e => setOverrideIri(e.target.value)}
                      />
                      <button
                        className="btn btn-ghost btn-sm"
                        onClick={() => handleDecision('override')}
                        disabled={!overrideIri.trim()}
                      >
                        Override
                      </button>
                    </div>
                    {decisionMsg && (
                      <div style={{
                        marginTop: 8, fontSize: 12,
                        color: decisionMsg.startsWith('Recorded')
                          ? '#166534' : '#991b1b',
                      }}>
                        {decisionMsg}
                      </div>
                    )}
                  </div>
                )}

                {(mapping.source_content_hash || mapping.source_file_audit_id) && (
                  <div style={{
                    borderTop: '1px solid #e5e7eb', paddingTop: 12, marginTop: 12,
                    fontSize: 10, color: '#9ca3af', fontFamily: 'monospace',
                  }}>
                    {mapping.source_content_hash && (
                      <div>hash: {mapping.source_content_hash.slice(0, 24)}…</div>
                    )}
                    {mapping.source_file_audit_id && (
                      <div>audit: {mapping.source_file_audit_id}</div>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </div>
    </>
  )
}
