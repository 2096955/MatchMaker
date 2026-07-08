import { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'
import { getCatalogueProduct, getMappingVendors, describeMappingAgent, runAgentStream } from '../../api'

function InfoRow({ label, value, mono }) {
  return (
    <div style={{ display: 'flex', gap: 8, padding: '6px 0', borderBottom: '1px solid #f3f4f6', alignItems: 'flex-start' }}>
      <span style={{ minWidth: 140, fontSize: 12, color: '#6b7280', flexShrink: 0 }}>{label}</span>
      <span style={{
        fontSize: 13,
        wordBreak: 'break-all',
        fontFamily: mono ? 'monospace' : undefined,
      }}>
        {value ?? '—'}
      </span>
    </div>
  )
}

function SectionCard({ title, children }) {
  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      <div style={{ padding: '10px 16px', borderBottom: '1px solid #e5e7eb', background: '#f9fafb' }}>
        <strong style={{ fontSize: 13 }}>{title}</strong>
      </div>
      <div style={{ padding: '10px 16px' }}>{children}</div>
    </div>
  )
}

function JsonBlock({ value }) {
  return (
    <pre style={{
      fontFamily: 'monospace',
      fontSize: 12,
      background: '#f9fafb',
      border: '1px solid #e5e7eb',
      borderRadius: 4,
      padding: '10px 14px',
      overflowX: 'auto',
      maxHeight: 360,
      overflowY: 'auto',
      whiteSpace: 'pre-wrap',
      wordBreak: 'break-word',
      color: '#374151',
      margin: 0,
    }}>
      {JSON.stringify(value, null, 2)}
    </pre>
  )
}

export default function CatalogueDetail() {
  const { vendor, ref } = useParams()
  const [product, setProduct]   = useState(null)
  const [loading, setLoading]   = useState(true)
  const [notFound, setNotFound] = useState(false)
  const [error, setError]       = useState('')

  // SCUDO Matcher Console state
  const [vendorsList, setVendorsList] = useState([])
  const [selectedVendor, setSelectedVendor] = useState(vendor)
  const [providers, setProviders] = useState([])
  const [selectedProvider, setSelectedProvider] = useState('bedrock')
  const [matchingLogs, setMatchingLogs] = useState([])
  const [matchingRunning, setMatchingRunning] = useState(false)
  const [matchingError, setMatchingError] = useState('')

  // Derived from providers/selectedProvider — recomputed every render so it
  // always reflects the latest describe_agent() response, never stale.
  const selectedProviderEntry = providers.find(p => p.id === selectedProvider)
  const selectedProviderEnabled = selectedProviderEntry?.enabled === true

  useEffect(() => {
    // Fetch available vendors
    getMappingVendors()
      .then(({ data }) => setVendorsList(data.vendors || []))
      .catch(() => setVendorsList(['lseg', 'spglobal', 'bloomberg', 'ice', 'factset']))

    // Fetch available providers
    describeMappingAgent()
      .then(({ data }) => {
        const fetchedProviders = data.providers || []
        setProviders(fetchedProviders)
        const defaultEntry = fetchedProviders.find(p => p.id === data.default_provider)
        if (defaultEntry && defaultEntry.enabled) {
          setSelectedProvider(defaultEntry.id)
        } else {
          // Described default is missing/disabled — fall back to the first
          // enabled provider rather than selecting a runtime that will
          // always fail.
          const firstEnabled = fetchedProviders.find(p => p.enabled)
          if (firstEnabled) setSelectedProvider(firstEnabled.id)
        }
      })
      .catch(() => {
        setProviders([
          { id: 'bedrock', label: 'Amazon Bedrock (Claude)', enabled: true },
          { id: 'azure', label: 'Azure OpenAI (ChatGPT 5.5 Med)', enabled: true }
        ])
      })
  }, [vendor])

  // Handlers for triggering SSE mapping run
  const triggerMatchingAgent = () => {
    if (!selectedProviderEnabled) {
      setMatchingError(
        `${selectedProviderEntry?.label || selectedProvider} is not configured on this deployment.`
      )
      return undefined
    }
    setMatchingLogs([])
    setMatchingError('')
    setMatchingRunning(true)

    const abortStream = runAgentStream(
      {
        vendor: selectedVendor,
        productId: product.vendor_product_ref,
        name: product.title,
        description: product.description,
        agentProvider: selectedProvider
      },
      (event) => {
        if (event.type === 'error') {
          setMatchingError(event.error)
          setMatchingRunning(false)
        } else if (event.type === 'done') {
          setMatchingRunning(false)
        } else {
          setMatchingLogs(prev => [...prev, event])
        }
      }
    )
    return abortStream
  }

  useEffect(() => {
    setLoading(true); setError(''); setNotFound(false)
    getCatalogueProduct(vendor, ref)
      .then(({ data }) => {
        setProduct(data)
        setSelectedVendor(data.vendor)
      })
      .catch(err => {
        if (err.response?.status === 404) setNotFound(true)
        else setError(err.response?.data?.error || 'Failed to load product')
      })
      .finally(() => setLoading(false))
  }, [vendor, ref])

  if (loading) {
    return <div style={{ padding: 40, textAlign: 'center' }}><span className="spinner" aria-label="Loading" /></div>
  }

  if (notFound) {
    return (
      <div className="page-header">
        <div>
          <div className="page-title">Product Not Found</div>
          <div className="page-sub">{vendor} / {ref}</div>
        </div>
        <Link to="/catalogue" className="btn btn-ghost">← Back to Catalogue</Link>
      </div>
    )
  }

  if (error) return <div className="alert alert-error">{error}</div>
  if (!product) return null

  return (
    <>
      <div className="page-header" style={{ maxWidth: '100%' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <Link to={`/catalogue?vendor=${product.vendor}`} className="btn btn-ghost btn-sm">← Back to Catalogue</Link>
          <div>
            <div className="page-title">{product.title}</div>
            <div className="page-sub">{product.vendor} · {product.vendor_product_ref}</div>
          </div>
        </div>
      </div>

      <div style={{ maxWidth: '100%', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 16 }}>
        <SectionCard title="Identity">
          <InfoRow label="IRI" value={product.iri} mono />
          <InfoRow label="Vendor" value={product.vendor} />
          <InfoRow label="Vendor Product Ref" value={product.vendor_product_ref} />
          <InfoRow label="Title" value={product.title} />
        </SectionCard>

        <SectionCard title="Provenance">
          <InfoRow label="Source" value={product.provenance?.source} />
          <InfoRow label="Snapshot" value={product.provenance?.source_snapshot} />
          <InfoRow
            label="Ingested at"
            value={product.provenance?.ingested_at
              ? new Date(product.provenance.ingested_at).toLocaleString()
              : null}
          />
          <InfoRow label="Version" value={product.provenance?.version} />
          <InfoRow label="Authority" value={product.provenance?.authority} />
        </SectionCard>

        <SectionCard title="Taxonomy">
          <InfoRow label="Theme" value={product.theme} />
          <InfoRow label="Asset Class" value={product.asset_class} />
          <InfoRow
            label="Keywords"
            value={product.keywords?.length ? (
              <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                {product.keywords.map(k => (
                  <span key={k} className="badge badge-gray">{k}</span>
                ))}
              </div>
            ) : null}
          />
          <InfoRow label="Description" value={product.description} />
        </SectionCard>

        <SectionCard title="Identifiers">
          {product.identifiers && Object.keys(product.identifiers).length > 0 ? (
            Object.entries(product.identifiers).map(([k, v]) => (
              <InfoRow key={k} label={k} value={v} mono />
            ))
          ) : (
            <span style={{ color: '#9ca3af', fontSize: 13 }}>No identifiers.</span>
          )}
        </SectionCard>
      </div>

      <div style={{ maxWidth: '100%', marginBottom: 16 }}>
        <SectionCard title="Raw Attributes (vendor-native carry-through)">
          {product.raw_attributes && Object.keys(product.raw_attributes).length > 0
            ? <JsonBlock value={product.raw_attributes} />
            : <span style={{ color: '#9ca3af', fontSize: 13 }}>No raw attributes recorded.</span>}
        </SectionCard>
      </div>

      <div style={{ maxWidth: '100%', marginBottom: 16 }}>
        <SectionCard title="SCUDO Semantic Matcher Console">
          <div style={{ display: 'flex', gap: 16, marginBottom: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span style={{ fontSize: 11, color: '#6b7280', fontWeight: 600 }}>Vendor Select</span>
              <select
                value={selectedVendor}
                onChange={e => setSelectedVendor(e.target.value)}
                disabled={matchingRunning}
                style={{ padding: '6px 10px', fontSize: 13, border: '1px solid #d1d5db', borderRadius: 4, height: 32 }}
              >
                {vendorsList.map(v => <option key={v} value={v}>{v}</option>)}
              </select>
            </div>

            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span style={{ fontSize: 11, color: '#6b7280', fontWeight: 600 }}>Inference Runtime</span>
              <select
                value={selectedProvider}
                onChange={e => setSelectedProvider(e.target.value)}
                disabled={matchingRunning}
                style={{ padding: '6px 10px', fontSize: 13, border: '1px solid #d1d5db', borderRadius: 4, height: 32 }}
              >
                {providers.map(p => (
                  <option key={p.id} value={p.id} disabled={!p.enabled}>
                    {p.label} {!p.enabled ? '(Disabled)' : ''}
                  </option>
                ))}
              </select>
            </div>

            <button
              className="btn btn-primary btn-sm"
              onClick={triggerMatchingAgent}
              disabled={matchingRunning || !selectedProviderEnabled}
              style={{ height: 32 }}
            >
              {matchingRunning ? 'Running SCUDO Agent...' : '▶ Run Matcher Agent'}
            </button>
          </div>

          {matchingError && (
            <div className="alert alert-error" style={{ margin: '10px 0 0 0', padding: '8px 12px', fontSize: 13 }}>
              {matchingError}
            </div>
          )}

          {matchingLogs.length > 0 && (
            <div style={{
              marginTop: 12,
              background: '#1e1e2e',
              color: '#a6accd',
              padding: '12px',
              borderRadius: 6,
              maxHeight: '300px',
              overflowY: 'auto',
              fontFamily: 'monospace',
              fontSize: '11px',
              lineHeight: '1.4'
            }}>
              {matchingLogs.map((log, i) => {
                let text = ''
                if (log.type === 'start') {
                  text = `>> Run started: Vendor=${log.vendor}, Product=${log.product_id}`
                } else if (log.type === 'tool_call') {
                  text = `[Tool Call] ${log.tool} with params: ${JSON.stringify(log.args)}`
                } else if (log.type === 'tool_result') {
                  text = `[Tool Result] Returned: ${JSON.stringify(log.result).slice(0, 100)}...`
                } else if (log.type === 'agent_message') {
                  text = `[Agent Message] ${log.content}`
                } else if (log.type === 'final_result') {
                  const m = log.mapping || {}
                  text = `>> [Final Result] Target: ${m.mapped_node_iri}, Confidence: ${m.confidence} (${m.band})`
                } else if (log.type === 'error') {
                  text = `!! [Error] ${log.error || ''}`
                }
                return <div key={i} style={{ borderBottom: '1px solid #2d2d3a', padding: '4px 0' }}>{text}</div>
              })}
            </div>
          )}
        </SectionCard>
      </div>
    </>
  )
}
