# SCUDO Agent Provider and Vendor Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement vendor-agnostic frontend and backend selection for SCUDO matching, supporting per-run provider selection (Bedrock or Azure OpenAI) and dynamic vendor discovery.

**Architecture:** Expose Vendor and Inference runtime dropdowns in the frontend Catalogue Detail view. Refactor the backend to cache and retrieve agents per-provider (Bedrock/Azure), and add an Azure OpenAI shim implementing the standard structured output contract.

**Tech Stack:** Python 3.11/3.12, Flask, Pydantic, openai-python (AzureOpenAI), React (Vite).

---

## Code Map
- `backend/scudo/schemas.py`: Add `agent_provider` to `IntakeRequest`.
- `backend/scudo/lambda_handler.py`: Add `AzureOpenAIShim`, refactor `_build_agents` and `_AGENTS` to a provider-keyed cache. Support `agent_provider` in payload.
- `backend/routes/mapping.py`: Add `providers` to `describe_agent`. Parse `agent_provider` in `run_agent` and pass to `get_agent`.
- `backend/scudo_mapping_mcp/agent.py`: Update `get_agent` to accept `provider` and handle it gracefully (e.g. log or fallback).
- `frontend/src/api/index.js`: Update `runAgentStream` to pass `agentProvider`.
- `frontend/src/pages/catalogue/CatalogueDetail.jsx`: Add a "SCUDO Matcher Console" panel with Vendor and Inference Runtime selectors, a "Run Matcher Agent" button, and streaming log terminal.

---

## Implementation Steps

### Task 1: Update Backend Pydantic Schemas

**Files:**
- Modify: `backend/scudo/schemas.py`

- [ ] **Step 1: Add `agent_provider` to `IntakeRequest`**

Modify `IntakeRequest` in `backend/scudo/schemas.py` to allow the optional `agent_provider` parameter:

```python
class IntakeRequest(BaseModel):
    """The deterministic intake stamps these flags before any agent reasons."""

    model_config = ConfigDict(extra="forbid")

    vendor: str = Field(..., examples=["lseg"])
    vendor_product_ref: str = Field(..., min_length=1, examples=["LSEG-IBES-EST-001"])
    has_precedent: bool = False
    has_conflict: bool = False
    ontology_gap: bool = False
    agent_provider: Optional[str] = Field(default=None, description="Inference runtime provider (bedrock or azure).")
```

- [ ] **Step 2: Verify `backend/scudo/schemas.py` imports and compiles successfully**

Run a syntax check:
`python -m py_compile backend/scudo/schemas.py`

---

### Task 2: Implement Azure OpenAI Shim and Backend Agent Caching

**Files:**
- Modify: `backend/scudo/lambda_handler.py`

- [ ] **Step 1: Implement `AzureOpenAIShim` in `backend/scudo/lambda_handler.py`**

Add the `AzureOpenAIShim` class definition to `backend/scudo/lambda_handler.py`:

```python
class AzureOpenAIShim:
    def __init__(self, system_prompt: str, deployment_env_var: str):
        self.system_prompt = system_prompt
        self.deployment_env_var = deployment_env_var

    def __call__(self, prompt: str, structured_output_model: Any) -> Any:
        parsed_result = self.structured_output(structured_output_model, prompt)
        class ResultWrapper:
            def __init__(self, val):
                self.structured_output = val
        return ResultWrapper(parsed_result)

    def structured_output(self, output_model: Any, prompt: str) -> Any:
        from openai import AzureOpenAI
        
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
        api_key = os.environ.get("AZURE_OPENAI_API_KEY")
        api_version = os.environ.get("AZURE_OPENAI_API_VERSION")
        deployment = os.environ.get(self.deployment_env_var)
        
        if not endpoint or not api_key:
            raise ValueError(
                f"Missing Azure OpenAI configuration. "
                "Ensure AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY are set."
            )
            
        client = AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=api_version,
        )
        
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": prompt}
        ]
        
        reasoning_effort = os.environ.get("AZURE_OPENAI_REASONING_EFFORT", "medium")
        
        try:
            response = client.beta.chat.completions.parse(
                model=deployment,
                messages=messages,
                response_format=output_model,
                reasoning_effort=reasoning_effort,
            )
            return response.choices[0].message.parsed
        except Exception:
            response = client.beta.chat.completions.parse(
                model=deployment,
                messages=messages,
                response_format=output_model,
            )
            return response.choices[0].message.parsed
```

- [ ] **Step 2: Refactor Agent caching and retrieval**

Change the single global `_AGENTS` tuple to a provider-keyed dictionary, and implement `_build_bedrock_agents`, `_build_azure_agents`, and `_get_agents_for_provider`:

```python
_AGENTS_CACHE: dict[str, tuple[Any, Any]] = {}

def _build_bedrock_agents() -> tuple[Agent, Agent]:
    model_id = bedrock_llm_id()
    region = aws_region()
    log.info("Bedrock model=%s region=%s", model_id, region)
    mapping_model = BedrockModel(model_id=model_id, region_name=region)
    verifier_model = BedrockModel(model_id=model_id, region_name=region)
    mapping = Agent(
        model=mapping_model,
        system_prompt=(
            "You are the SCUDO Mapping Specialist. Map ONE vendor product to "
            "ONE CDAO node from bundle.candidates. Cite at least one Evidence "
            "entry whose source_iris contain BOTH the chosen candidate IRI and "
            "the ontology_snapshot value. Set confidence_band: high>=0.8, "
            "medium>=0.5, low<0.5. Leave proposed_triples empty; the "
            "orchestrator serialises deterministic DCAT triples."
        ),
    )
    verifier = Agent(
        model=verifier_model,
        system_prompt=(
            "You are the SCUDO Verifier. Score MappingResult on the 10-"
            "dimension rubric (0/1/2 each). Do not redo the mapping; assess "
            "it. taxonomy_freshness=2 only if the ontology_snapshot appears "
            "in any Evidence entry."
        ),
    )
    return mapping, verifier

def _build_azure_agents() -> tuple[AzureOpenAIShim, AzureOpenAIShim]:
    log.info("Building Azure OpenAI shims")
    mapping_prompt = (
        "You are the SCUDO Mapping Specialist. Map ONE vendor product to "
        "ONE CDAO node from bundle.candidates. Cite at least one Evidence "
        "entry whose source_iris contain BOTH the chosen candidate IRI and "
        "the ontology_snapshot value. Set confidence_band: high>=0.8, "
        "medium>=0.5, low<0.5. Leave proposed_triples empty; the "
        "orchestrator serialises deterministic DCAT triples."
    )
    verifier_prompt = (
        "You are the SCUDO Verifier. Score MappingResult on the 10-"
        "dimension rubric (0/1/2 each). Do not redo the mapping; assess "
        "it. taxonomy_freshness=2 only if the ontology_snapshot appears "
        "in any Evidence entry."
    )
    
    mapping = AzureOpenAIShim(
        system_prompt=mapping_prompt,
        deployment_env_var="AZURE_OPENAI_SPECIALIST_DEPLOYMENT",
    )
    verifier = AzureOpenAIShim(
        system_prompt=verifier_prompt,
        deployment_env_var="AZURE_OPENAI_VERIFIER_DEPLOYMENT",
    )
    return mapping, verifier

def _get_agents_for_provider(provider: str) -> tuple[Any, Any]:
    global _AGENTS_CACHE
    if provider not in _AGENTS_CACHE:
        if provider == "azure":
            _AGENTS_CACHE[provider] = _build_azure_agents()
        else:
            _AGENTS_CACHE[provider] = _build_bedrock_agents()
    return _AGENTS_CACHE[provider]
```

- [ ] **Step 3: Update `handler` to retrieve agents dynamically**

In `backend/scudo/lambda_handler.py`'s `handler()`:

```python
    # Determine provider from payload, falling back to SCUDO_AGENT_PROVIDER_DEFAULT or "bedrock"
    provider_default = os.environ.get("SCUDO_AGENT_PROVIDER_DEFAULT", "bedrock").strip().lower()
    agent_provider = (payload.get("agent_provider") or provider_default).strip().lower()
    if agent_provider not in ("bedrock", "azure"):
        return _resp(400, {"error": f"unknown agent provider: {agent_provider}"})

    try:
        IntakeRequest.model_validate(
            {
                "vendor": payload.get("vendor", ""),
                "vendor_product_ref": payload.get("vendor_product_ref", ""),
                "has_precedent": bool(payload.get("has_precedent", False)),
                "has_conflict": bool(payload.get("has_conflict", False)),
                "ontology_gap": bool(payload.get("ontology_gap", False)),
                "agent_provider": agent_provider,
            }
        )
    except Exception as e:
        return _resp(400, {"error": f"invalid intake: {e}"})

    mapping_agent, verifier_agent = _get_agents_for_provider(agent_provider)
```

- [ ] **Step 4: Verify `backend/scudo/lambda_handler.py` compiles successfully**

Run:
`python -m py_compile backend/scudo/lambda_handler.py`

---

### Task 3: Enhance Agent Route and Provider Discovery

**Files:**
- Modify: `backend/routes/mapping.py`
- Modify: `backend/scudo_mapping_mcp/agent.py`

- [ ] **Step 1: Enhance `describe_agent` endpoint in `backend/routes/mapping.py`**

Update `describe_agent` to return registry/providers information:

```python
@mapping_bp.get("/mapping/agent/describe")
def describe_agent():
    """Tell the frontend which agent backend is wired and the available providers."""
    backend = (os.getenv("SCUDO_AGENT_BACKEND") or "scripted").strip().lower()
    default_provider = (os.getenv("SCUDO_AGENT_PROVIDER_DEFAULT") or "bedrock").strip().lower()
    
    providers = [
        {
            "id": "bedrock",
            "label": "Amazon Bedrock (Claude)",
            "enabled": True,
        },
        {
            "id": "azure",
            "label": "Azure OpenAI (ChatGPT 5.5 Med)",
            "enabled": bool(os.getenv("AZURE_OPENAI_ENDPOINT")),
        }
    ]
    
    return jsonify(
        {
            "backend": backend,
            "default_provider": default_provider,
            "providers": providers,
            "model_id": (
                os.getenv("SCUDO_BEDROCK_MODEL_ID") or "eu.anthropic.claude-opus-4-8"
                if backend == "bedrock"
                else None
            ),
            "region": (
                os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
                if backend == "bedrock"
                else None
            ),
        }
    )
```

- [ ] **Step 2: Update `run_agent()` and `get_agent()` to accept `provider`**

In `backend/routes/mapping.py`'s `run_agent()`:

```python
    body = request.get_json(silent=True) or {}
    vendor = (body.get("vendor") or "").strip()
    product_id = (body.get("product_id") or "").strip()
    agent_provider = (body.get("agent_provider") or "").strip().lower() or None
    # ...
    agent = get_agent(provider=agent_provider)
```

In `backend/scudo_mapping_mcp/agent.py`'s `get_agent()`:

```python
def get_agent(provider: Optional[str] = None) -> Any:
    """Return the configured mapping agent, taking provider into account."""
    backend = (os.getenv("SCUDO_AGENT_BACKEND") or "scripted").strip().lower()
    use_host = _env_truthy(os.getenv("SCUDO_MCP_HOST_ENABLED"))
    if backend == "bedrock":
        # Handled gracefully, logging if provider is azure but streaming uses Bedrock mapping agent
        if provider == "azure":
            ui_logger.warning("Streaming agent requested 'azure' provider; using BedrockMappingAgent fallback")
        return BedrockMappingAgent(use_mcp_host=use_host)
    return ScriptedMappingAgent(use_mcp_host=use_host)
```

- [ ] **Step 3: Verify all modified backend files compile successfully**

Run:
`python -m py_compile backend/routes/mapping.py backend/scudo_mapping_mcp/agent.py`

---

### Task 4: Enhance Frontend API Layer

**Files:**
- Modify: `frontend/src/api/index.js`

- [ ] **Step 1: Support `agentProvider` in `runAgentStream`**

```javascript
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
      // ... rest unchanged ...
```

---

### Task 5: Expose Dropdowns and Create SCUDO Matcher Console in UI

**Files:**
- Modify: `frontend/src/pages/catalogue/CatalogueDetail.jsx`

- [ ] **Step 1: Integrate SCUDO Matcher Console into product details**

Expose the Vendor dropdown and the Inference runtime dropdown, and allow users to run the SCUDO matcher streaming interface directly on the catalogue product page!

Let's implement a clean, interactive section:

```javascript
// Import runAgentStream and getMappingVendors / describeMappingAgent at the top
import { getCatalogueProduct, getMappingVendors, describeMappingAgent, runAgentStream } from '../../api'

// Add state and streaming execution to CatalogueDetail component:
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

  useEffect(() => {
    // Fetch available vendors
    getMappingVendors()
      .then(({ data }) => setVendorsList(data.vendors || []))
      .catch(() => setVendorsList(['lseg', 'spglobal', 'bloomberg', 'ice', 'factset']))

    // Fetch available providers
    describeMappingAgent()
      .then(({ data }) => {
        setProviders(data.providers || [])
        if (data.default_provider) {
          setSelectedProvider(data.default_provider)
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
```

Then append a new `SectionCard` showing the SCUDO Matcher Console:

```jsx
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
      disabled={matchingRunning}
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
          text = `[Tool Call] ${log.tool_name} with params: ${JSON.stringify(log.arguments)}`
        } else if (log.type === 'tool_result') {
          text = `[Tool Result] Returned: ${JSON.stringify(log.result).slice(0, 100)}...`
        } else if (log.type === 'agent_message') {
          text = `[Agent Message] ${log.message}`
        } else if (log.type === 'final_result') {
          text = `>> [Final Result] Target: ${log.proposed_target_iri}, Confidence: ${log.confidence} (${log.band})`
        }
        return <div key={i} style={{ borderBottom: '1px solid #2d2d3a', padding: '4px 0' }}>{text}</div>
      })}
    </div>
  )}
</SectionCard>
```

---

## Test & Verification Plan

### Backend Verification
- Add a new pytest file `backend/scudo/tests/test_agent_provider.py`.
- **Verify unknown `agent_provider`**: Check that passing an invalid provider to `lambda_handler` rejects the request with a `400` status code.
- **Verify `agent_provider` discovery**: Perform a mock GET request on `/api/mapping/agent/describe` and assert that the provider list is returned.
- **Verify Azure OpenAI Shim**: Test that the `AzureOpenAIShim` correctly formats outputs using a mock AzureOpenAI client, and that it falls back gracefully if `reasoning_effort` raises an error.

To run tests:
`pytest backend/scudo/tests/test_agent_provider.py -v`

### Frontend Verification
- Run `npm run build` in `frontend/` to ensure no syntax/type/compilation issues exist in React components.

---

## Rollback Notes
- To revert, delete `backend/scudo/tests/test_agent_provider.py`.
- Revert modifications to:
  - `backend/scudo/schemas.py`
  - `backend/scudo/lambda_handler.py`
  - `backend/routes/mapping.py`
  - `backend/scudo_mapping_mcp/agent.py`
  - `frontend/src/api/index.js`
  - `frontend/src/pages/catalogue/CatalogueDetail.jsx`
