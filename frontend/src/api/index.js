import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

// ── Providers ────────────────────────────────────────────────────────
export const getProviders   = (q = '') => api.get('/providers', { params: { q } })
export const getProvider    = (id)     => api.get(`/providers/${id}`)
export const addProvider    = (data)   => api.post('/providers', data)
export const editProvider   = (id, d)  => api.put(`/providers/${id}`, d)
export const deleteProvider = (id)     => api.delete(`/providers/${id}`)

// ── Datasets ─────────────────────────────────────────────────────────
export const getDatasets  = (q = '', provider_id = '') =>
  api.get('/datasets', { params: { q, provider_id } })
export const getDataset   = (id)    => api.get(`/datasets/${id}`)
export const addDataset   = (data)  => api.post('/datasets', data)
export const editDataset  = (id, d) => api.put(`/datasets/${id}`, d)
export const deleteDataset = (id)   => api.delete(`/datasets/${id}`)

export const uploadFile = (file, data_format) => {
  const form = new FormData()
  form.append('file', file)
  form.append('data_format', data_format)
  return api.post('/datasets/upload', form, { headers: { 'Content-Type': 'multipart/form-data' } })
}

// ── Roles ─────────────────────────────────────────────────────────────
export const getRoles  = ()       => api.get('/roles')
export const getRole   = (id)     => api.get(`/roles/${id}`)
export const addRole   = (data)   => api.post('/roles', data)
export const editRole  = (id, d)  => api.put(`/roles/${id}`, d)
export const deleteRole = (id)    => api.delete(`/roles/${id}`)

// ── Users ─────────────────────────────────────────────────────────────
export const getUsers  = ()       => api.get('/users')
export const getUser   = (id)     => api.get(`/users/${id}`)
export const addUser   = (data)   => api.post('/users', data)
export const editUser  = (id, d)  => api.put(`/users/${id}`, d)
export const deleteUser = (id)    => api.delete(`/users/${id}`)

// ── Transforms ───────────────────────────────────────────────────────
export const getTransforms  = (did)        => api.get(`/datasets/${did}/transforms`)
export const saveTransforms = (did, rows)  => api.put(`/datasets/${did}/transforms`, rows)

// ── Reports ───────────────────────────────────────────────────────────
export const getReports = (params = {}) => api.get('/reports', { params })
export const getReport  = (id)          => api.get(`/reports/${id}`)

// ── Ingest trigger ────────────────────────────────────────────────────
export const triggerIngest = (dataset_id) => api.post(`/ingest/${dataset_id}`)

// ── Vendor Catalogue (thin HTTP facade over vendor_catalogue_mcp) ────
// Mirrors the four MCP tools — list / get / schema / delta — plus a
// vendors helper so the UI dropdown stays in sync with the contract enum.
export const getCatalogueVendors = () => api.get('/catalogue/vendors')

export const getCatalogueProducts = (params = {}) =>
  api.get('/catalogue/products', { params })

export const getCatalogueProduct = (vendor, vendorProductRef) =>
  api.get(`/catalogue/products/${vendor}/${encodeURIComponent(vendorProductRef)}`)

export const getCatalogueSchema = (vendor) =>
  api.get('/catalogue/schema', { params: { vendor } })

export const getCatalogueDelta = (vendor, since) =>
  api.get('/catalogue/delta', { params: { vendor, since } })

// ── SCUDO Mapping (thin HTTP facade over scudo_mapping_mcp) ──────────
// One JSON-shaped client per HTTP endpoint. The /mapping/agent/run
// route is SSE and is not exposed here — pages that need the stream
// call fetch() directly (see runAgentStream below).
export const getMappingVendors = () => api.get('/mapping/vendors')

export const listMappingWorkingSet = () => api.get('/mapping/working_set')

export const describeMappingAgent = () => api.get('/mapping/agent/describe')

export const ingestMappingFile = (vendor, file) => {
  const form = new FormData()
  form.append('vendor', vendor)
  form.append('file', file)
  return api.post('/mapping/ingest', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

export const mapVendorProduct = (body) => api.post('/mapping/map', body)

// Ingestion mock — paste a vendor row, get a VendorProductRef-shaped frame.
// Pure demo seam; nothing persists. See backend/routes/ingestion_mock.py.
export const ingestionMock = (body) =>
  api.post('/mapping/ingestion-mock', body)

export const recordMappingDecision = (body) =>
  api.post('/mapping/decision', body)

// ── iFusion publish (mock SPI V2 — demo only) ────────────────────────
// The matcher persists into Neptune; this endpoint pretends to publish the
// canonical mapping downstream so the demo can show the full loop closing.
// Idempotent on (vendor, product_id, mapping_id) — replay returns the same
// publish_id with status='duplicate'. No HMAC verification (post-persist).
export const publishMappingToIfusion = ({ mappingId, vendor, productId }) =>
  api.post('/ifusion/publish', {
    mapping_id: mappingId,
    vendor,
    product_id: productId,
  })

export const listRecentIfusionPublishes = (limit = 20) =>
  api.get('/ifusion/recent', { params: { limit } })

// Shared SSE-frame consumer for both /mapping/agent/run and
// /mapping/ingest/stream - both stream `data: {...}\n\n` frames the same
// way; this is the one place that parses that wire format.
async function _consumeSSE(resp, onEvent) {
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}))
    onEvent({ type: 'error', error: body.error || `HTTP ${resp.status}` })
    return
  }
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const frames = buf.split('\n\n')
    buf = frames.pop() // keep the partial frame
    for (const frame of frames) {
      const line = frame.split('\n').find(l => l.startsWith('data: '))
      if (!line) continue
      try {
        onEvent(JSON.parse(line.slice(6)))
      } catch {
        // Ignore malformed event; the next frame is likely valid.
      }
    }
  }
}

// runAgentStream streams Server-Sent Events from POST /mapping/agent/run.
// EventSource doesn't support POST, so we use fetch + ReadableStream.
// onEvent is called with each parsed AgentEvent (type + payload fields).
// Returns a function that aborts the stream when called.
export const runAgentStream = ({ vendor, productId, name, description, agentProvider }, onEvent) => {
  const controller = new AbortController()
  ;(async () => {
    try {
      const resp = await fetch('/api/mapping/agent/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          vendor,
          product_id: productId,
          name: name || '',
          description: description || '',
          agent_provider: agentProvider || '',
        }),
        signal: controller.signal,
      })
      await _consumeSSE(resp, onEvent)
    } catch (err) {
      if (err.name !== 'AbortError') {
        onEvent({ type: 'error', error: err.message || String(err) })
      }
    }
  })()
  return () => controller.abort()
}

// ingestMappingFileStream streams Server-Sent Events from POST
// /mapping/ingest/stream (multipart vendor + file) - same wire format and
// consumption helper as runAgentStream above, so the UI can show real ETL
// stage progress instead of an instant fake success.
export const ingestMappingFileStream = ({ vendor, file }, onEvent) => {
  const controller = new AbortController()
  ;(async () => {
    try {
      const form = new FormData()
      form.append('vendor', vendor)
      form.append('file', file)
      const resp = await fetch('/api/mapping/ingest/stream', {
        method: 'POST',
        body: form,
        signal: controller.signal,
      })
      await _consumeSSE(resp, onEvent)
    } catch (err) {
      if (err.name !== 'AbortError') {
        onEvent({ type: 'error', error: err.message || String(err) })
      }
    }
  })()
  return () => controller.abort()
}

// ingestMappingUrl posts a website URL for server-side fetch + ingestion
// (POST /mapping/ingest/url). Not streamed - a single JSON response, same
// shape as ingestMappingFile above.
export const ingestMappingUrl = (vendor, url) =>
  api.post('/mapping/ingest/url', { vendor, url })
