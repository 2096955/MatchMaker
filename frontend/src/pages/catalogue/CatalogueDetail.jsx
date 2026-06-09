import { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'
import { getCatalogueProduct } from '../../api'

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

  useEffect(() => {
    setLoading(true); setError(''); setNotFound(false)
    getCatalogueProduct(vendor, ref)
      .then(({ data }) => setProduct(data))
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

      <div style={{ maxWidth: '100%' }}>
        <SectionCard title="Raw Attributes (vendor-native carry-through)">
          {product.raw_attributes && Object.keys(product.raw_attributes).length > 0
            ? <JsonBlock value={product.raw_attributes} />
            : <span style={{ color: '#9ca3af', fontSize: 13 }}>No raw attributes recorded.</span>}
        </SectionCard>
      </div>
    </>
  )
}
