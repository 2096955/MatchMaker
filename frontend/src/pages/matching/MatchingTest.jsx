import { useEffect, useState } from 'react'
import { describeMappingAgent, ingestMappingFileStream, ingestMappingUrl, runAgentStream } from '../../api'

// One line of the agent's reasoning trace.
//
// The backend streams a full trace over SSE — start · agent_message ·
// tool_call · tool_result · final_result · done (verified: a real run emits
// 4 tool_call + 4 tool_result + 3 agent_message). This used to render as
// `e.type: {raw JSON}` truncated at 120 chars, which cut the agent's own
// sentences off mid-word and showed a candidate list as unreadable JSON.
// The information was arriving; it just was not legible.
function AgentStep({ event }) {
  const row = { display: 'flex', gap: 8, padding: '4px 0', alignItems: 'baseline' }
  const tag = { fontSize: 11, fontWeight: 600, minWidth: 74, color: '#6b7280' }

  if (event.type === 'agent_message') {
    return (
      <div style={row}>
        <span style={tag}>thinking</span>
        <span style={{ fontStyle: 'italic' }}>{event.content}</span>
      </div>
    )
  }

  if (event.type === 'tool_call') {
    const args = event.args || {}
    // Show the arguments that identify WHAT was asked, not the whole payload.
    const summary = args.node_iri || args.name || args.product_id || ''
    return (
      <div style={row}>
        <span style={tag}>calls</span>
        <span>
          <code>{event.tool}</code>
          {summary ? <span style={{ color: '#6b7280' }}> — {summary}</span> : null}
        </span>
      </div>
    )
  }

  if (event.type === 'tool_result') {
    // Each tool returns a different shape; surface the one number or label a
    // reader actually needs, and fall back to a compact count.
    const r = event.result || {}
    let summary
    if (Array.isArray(r.candidates)) {
      const top = r.candidates[0]
      const label = top?.node?.label ?? top?.label
      const score = top?.similarity ?? top?.score
      summary = label
        ? `${r.candidates.length} candidates — top: ${label}${
            typeof score === 'number' ? ` (${score.toFixed(2)})` : ''
          }`
        : `${r.candidates.length} candidates`
    } else if (r.label) {
      summary = r.label
    } else if (typeof r.count === 'number') {
      summary = `${r.count} results`
    } else {
      summary = Object.keys(r).slice(0, 3).join(', ') || 'ok'
    }
    return (
      <div style={row}>
        <span style={tag}>returns</span>
        <span style={{ color: '#374151' }}>{summary}</span>
      </div>
    )
  }

  if (event.type === 'start') {
    return (
      <div style={row}>
        <span style={tag}>start</span>
        <span style={{ color: '#6b7280' }}>
          {event.product_name || event.product_id} · {event.agent_backend}
        </span>
      </div>
    )
  }

  // final_result is rendered in full by the Match result card below, and
  // error by the banner above. Showing either here would duplicate them —
  // and as raw JSON, which is what this component exists to avoid.
  if (event.type === 'done' || event.type === 'final_result' || event.type === 'error') {
    return null
  }

  // Unknown event type: show it rather than swallow it, so a new backend
  // event is visible instead of silently dropped.
  return (
    <div style={row}>
      <span style={tag}>{event.type}</span>
      <span style={{ color: '#6b7280' }}>{JSON.stringify(event).slice(0, 160)}</span>
    </div>
  )
}

export default function MatchingTest() {
  const [vendor, setVendor] = useState('LSEG')
  const [providers, setProviders] = useState([])
  // JPMC-LOCAL: fail CLOSED to the offline narrator. This used to start on
  // 'bedrock', and the describe() below silently swallowed its own failure --
  // so if /agent/describe was slow or errored, pressing Run sent
  // agent_provider='bedrock', which get_agent() honours over
  // SCUDO_AGENT_BACKEND. On a laptop with no credentials that is a confusing
  // AWS error; on a machine WITH credentials it is a real call nobody asked
  // for. 'scripted' is always safe, and the real default replaces it as soon
  // as describe() answers.
  const [provider, setProvider] = useState('scripted')
  const [file, setFile] = useState(null)
  const [url, setUrl] = useState('')
  const [ingestLog, setIngestLog] = useState([])
  const [ingestResult, setIngestResult] = useState(null)
  const [matchLog, setMatchLog] = useState([])
  const [matchResult, setMatchResult] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    describeMappingAgent()
      .then(({ data }) => {
        setProviders(data.providers || [])
        if (data.default_provider) setProvider(data.default_provider)
      })
      // JPMC-LOCAL: was `.catch(() => {})` -- a failed discovery left the
      // dropdown empty with no explanation. Say so instead; the provider
      // stays on the safe offline default above.
      .catch(() => setError(
        'Could not read /api/mapping/agent/describe — provider list unavailable. '
        + 'Falling back to the offline "scripted" runtime.'
      ))
  }, [])

  const reset = () => {
    setError(null)
    setIngestLog([])
    setIngestResult(null)
    setMatchLog([])
    setMatchResult(null)
  }

  const onFileEvent = (event) => {
    if (event.type === 'error') { setError(event.error); return }
    if (event.type === 'stage') setIngestLog(l => [...l, event])
    if (event.type === 'final_result') setIngestResult(event)
  }

  const submitFile = () => {
    reset()
    if (!file) { setError('choose a file first'); return }
    ingestMappingFileStream({ vendor, file }, onFileEvent)
  }

  const submitUrl = async () => {
    reset()
    if (!url) { setError('enter a URL first'); return }
    try {
      const { data } = await ingestMappingUrl(vendor, url)
      setIngestResult({ type: 'final_result', ...data })
    } catch (err) {
      setError(err.response?.data?.error || err.message)
    }
  }

  const runMatch = () => {
    setMatchLog([])
    setMatchResult(null)
    setError(null)
    const product = ingestResult?.products?.[0]
    if (!product) { setError('ingest a file or URL first'); return }
    runAgentStream(
      { vendor, productId: product.product_id, name: product.name, agentProvider: provider },
      (event) => {
        if (event.type === 'error') { setError(event.error); return }
        if (event.type === 'final_result') { setMatchResult(event); return }
        setMatchLog(l => [...l, event])
      }
    )
  }

  return (
    <>
      <div className="page-header">
        <div>
          <div className="page-title">Matching Test</div>
          <div className="page-sub">
            Drive the real matching pipeline: upload a file or submit a URL, then run the agent.
          </div>
        </div>
      </div>

      <div className="card" style={{ padding: 16, marginBottom: 16, maxWidth: 640 }}>
        <label>
          Vendor{' '}
          <input data-testid="vendor-input" value={vendor} onChange={e => setVendor(e.target.value)} />
        </label>
        <div style={{ marginTop: 10 }}>
          <label>
            Provider{' '}
            <select
              data-testid="provider-select"
              value={provider}
              onChange={e => setProvider(e.target.value)}
            >
              {providers.map(p => (
                <option key={p.id} value={p.id} disabled={!p.enabled}>
                  {p.label}{!p.enabled ? ' (not configured)' : ''}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>

      <div className="card" style={{ padding: 16, marginBottom: 16, maxWidth: 640 }}>
        <div style={{ fontWeight: 700, marginBottom: 8 }}>File upload</div>
        <input data-testid="file-input" type="file" onChange={e => setFile(e.target.files?.[0] || null)} />
        <button data-testid="submit-file" className="btn btn-primary btn-sm" onClick={submitFile} style={{ marginLeft: 8 }}>
          Ingest file
        </button>
      </div>

      <div className="card" style={{ padding: 16, marginBottom: 16, maxWidth: 640 }}>
        <div style={{ fontWeight: 700, marginBottom: 8 }}>Website URL</div>
        <input data-testid="url-input" value={url} onChange={e => setUrl(e.target.value)} style={{ width: 360 }} />
        <button data-testid="submit-url" className="btn btn-primary btn-sm" onClick={submitUrl} style={{ marginLeft: 8 }}>
          Ingest URL
        </button>
      </div>

      {error && (
        <div data-testid="error-banner" className="alert alert-error" style={{ maxWidth: 640 }}>
          ✗ {error}
        </div>
      )}

      {ingestLog.length > 0 && (
        <div style={{ fontSize: 12, color: '#6b7280', maxWidth: 640 }}>
          {ingestLog.map((e, i) => <div key={i}>{e.stage}: {JSON.stringify(e.detail)}</div>)}
        </div>
      )}

      {ingestResult && (
        <div data-testid="ingest-result" className="card" style={{ padding: 16, marginBottom: 16, maxWidth: 640 }}>
          <div style={{ fontWeight: 700 }}>Ingested {ingestResult.ingested} product(s)</div>
          {(ingestResult.products || []).map(p => (
            <div key={p.product_id} style={{ fontSize: 13 }}>{p.name} ({p.product_id})</div>
          ))}
          <button className="btn btn-primary btn-sm" data-testid="run-match" onClick={runMatch} style={{ marginTop: 10 }}>
            Run match
          </button>
        </div>
      )}

      {matchLog.length > 0 && (
        <div
          data-testid="agent-reasoning"
          className="card"
          style={{ padding: 16, marginBottom: 16, maxWidth: 640 }}
        >
          <div style={{ fontWeight: 700, marginBottom: 8 }}>Agent reasoning</div>
          <div style={{ fontSize: 13, color: '#374151' }}>
            {matchLog.map((e, i) => (
              <AgentStep key={i} event={e} />
            ))}
          </div>
        </div>
      )}

      {matchResult && (
        <div data-testid="match-result" className="card" style={{ padding: 16, maxWidth: 640 }}>
          <div style={{ fontWeight: 700 }}>Match result</div>
          <div>Confidence: {matchResult.confidence ?? matchResult.result?.confidence ?? 'n/a'}</div>
          <div>Provider: {provider}</div>
          <pre style={{ fontSize: 11, whiteSpace: 'pre-wrap' }}>{JSON.stringify(matchResult, null, 2)}</pre>
        </div>
      )}
    </>
  )
}
