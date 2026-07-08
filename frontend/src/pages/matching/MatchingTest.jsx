import { useEffect, useState } from 'react'
import { describeMappingAgent, ingestMappingFileStream, ingestMappingUrl, runAgentStream } from '../../api'

export default function MatchingTest() {
  const [vendor, setVendor] = useState('LSEG')
  const [providers, setProviders] = useState([])
  const [provider, setProvider] = useState('bedrock')
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
      .catch(() => {})
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
        <div style={{ fontSize: 12, color: '#6b7280', maxWidth: 640 }}>
          {matchLog.map((e, i) => <div key={i}>{e.type}: {JSON.stringify(e).slice(0, 120)}</div>)}
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
