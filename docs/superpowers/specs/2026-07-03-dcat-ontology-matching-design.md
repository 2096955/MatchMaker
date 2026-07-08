# DCAT / SKOS Ontology Matching — Design Spec

**Date:** 2026-07-03  
**Status:** P0 implemented (reviewable); P1–P4 deferred  
**Repo:** `MatchMaker/MatchMaker` only (not Defra)

> **Restoration note (2026-07-03):** This file was present at the start of the P0
> implementation session and was **accidentally deleted** while narrowing the P0
> *code* diff (docs are not part of the runtime diff). It was **not** in git
> history. This copy is restored from session evidence, residual-gap verification,
> and the P0 implementation on disk. Wording may differ slightly from the
> pre-deletion draft; behaviour and phasing match current tree.

---

## 1. Problem & motivation

MatchMaker’s matcher seam today seeds a **CDAO JSON catalogue** and scores vendor
products against flat `TaxonomyNode` labels. Client ontologies arrive as **RDF /
Turtle / JSON-LD** with SKOS definitions, alt labels, and RDFS/OWL subsumption.
Without a loader and richer node shape, DCAT/SKOS semantics never reach BM25
nomination or dense-arm candidate text.

**Goal:** ingest DCAT/SKOS (and related OWL/DCAT entities) into the existing
`TaxonomyNode` seam behind `SCUDO_TAXONOMY_LOADER`, with **backward-compatible
defaults** and **flag-gated** text injection so legacy behaviour stays default.

**Non-goals (this spec):** Defra workstreams; full multi-path subsumption
expansion; real SHACL/ODRL backends; enrichment→matcher projection; CSVW upload
surface.

---

## 2. Matcher invariants (load-bearing)

These pre-date ontology work and must not regress:

| ID | Invariant |
|----|-----------|
| **I-dense** | `Candidate.similarity` is the **raw dense-arm score** only (Jaro-Winkler, Opus per-pair, or Opus multi-path). Never BM25. Never RRF. Never rank-signal boost. |
| **I-bm25** | BM25 is a **nominator** (pre-filter / lexical sidecar). BM25 scores must not populate `Candidate.similarity`. |
| **I-floor** | The 0.80 confidence floor is calibrated against dense similarity; fused ranks must not lift sub-floor candidates. |
| **I-enrich** | `enrichment.py` is metadata-only; it **never** feeds `matching.py` (I5). |

P0 ontology text injection widens **recall** (BM25 docs, dense `candidate_desc`) but
must honour I-dense and I-bm25.

---

## 3. Workstreams

| WS | Name | P0 slice | Later |
|----|------|----------|-------|
| **WS1** | RDF/Turtle DCAT+SKOS taxonomy loader | `loaders/dcat_loader.py`, `taxonomy_loader.py`, `rdflib` | Load-time RDFS closure (P1) |
| **WS3** | Extended `TaxonomyNode` model | Default-valued fields on existing model | DCAT entity projection into matcher (P1 WS4) |
| **WS7** | SKOS text into matching paths | `taxonomy_text.py` + store/retrieval threading; `SCUDO_TAXONOMY_TEXT` | Shadow rollout, calibration (P1) |
| **WS4** | Enrichment projection | — | P1 |
| **WS5** | Real RDF + SHACL | — | P2 (`pyshacl`, `scudo/rdf/real.py`) |
| **WS6** | ODRL rights | — | P3 (`rights_odrl.py`) |
| **WS7b** | CSVW / upload Turtle | — | P4 |

---

## 4. Phasing

### P0 (reviewable — **implemented on disk**)

- **WS1:** Parse `.ttl` / `.rdf` / `.xml` / `.jsonld` via rdflib; dispatch through
  `SCUDO_TAXONOMY_LOADER` (`cdao` | `dcat`); `SCUDO_TAXONOMY_SOURCE` required for
  `dcat`.
- **WS3:** Extend `TaxonomyNode` with `definition`, `alt_labels`, `node_kind`,
  `superclass_iris`, `superproperty_iris` (all default-empty / safe).
- **WS7 (P0 slice):** `taxonomy_text.py` helpers; thread into **stores** (FalkorDB,
  memory) and **retrieval** BM25 prefilter; gate with `SCUDO_TAXONOMY_TEXT` (default
  off). Do **not** require changes to `opus_dense.py`, `specialist.py`, or
  `enrichment.py` for P0 sign-off.

**P0 does not include:** subsumption closure/expansion, `models_dcat.py`,
calibration harness, real RDF/SHACL, ODRL, CSVW, smoke.py churn.

### P1

- RDFS subsumption closure at load time; 1-hop BM25 candidate expansion in
  `retrieval.multi_path_retrieve`.
- Enrichment→matcher DCAT projection (WS4).
- Confidence-floor calibration harness; shadow `SCUDO_TAXONOMY_TEXT` rollout.

### P2 — WS5

- `SCUDO_RDF_BACKEND`, `scudo/rdf/real.py`, `pyshacl`, SHACL shapes.

### P3 — WS6

- ODRL evaluator; `check_scope` integration.

### P4

- CSVW aliases; `TurtleIngester` on upload surface.
- **Catalogue ontology POC** (transcript-derived, unverified):
  `tests/fixtures/catalogue_ontology_v0_1_deontic.ttl` — prefixes normalized to
  canonical vocab IRIs; `cat:` placeholder
  `https://example.org/catalogue/ontology/v0.1/deontic/`.
- RDF CSVW seam: `csvw_metadata_from_rdf()` / `csvw_aliases_from_graph()` read
  `csvw:name` (plus `dcterms:title` `rdfs:label`) for `ingest_bytes(csvw_metadata=…)`.

---

## 5. WS1 — DCAT/SKOS loader

### Dispatch (`loaders/taxonomy_loader.py`)

- `cdao` → canonical JSON fixture (`scudo/fixtures/cdao_catalogue.json`).
- `dcat` → `load_dcat_taxonomy(path)` (lazy rdflib import).
- Registry `_TAXONOMY_LOADERS` keys must equal `config.ALLOWED_TAXONOMY_LOADERS`
  (asserted at import).

### Parser (`loaders/dcat_loader.py`)

**Entity types:** `skos:Concept`, `owl:Class` / `rdfs:Class`, RDF/OWL properties,
`dcat:Dataset`, `dcat:Distribution`, `dcat:DataService`.

**Extracted predicates:**

| RDF | `TaxonomyNode` field |
|-----|----------------------|
| `skos:prefLabel`, `dct:title`, `rdfs:label` | `label` |
| `skos:definition`, `skos:scopeNote`, `skos:example`, `dct:description`, `rdfs:comment` | `definition` (joined, capped 2000 chars) |
| `skos:altLabel`, `dcat:keyword` | `alt_labels` |
| `skos:broader` | `parent_iri` |
| `rdfs:subClassOf`, `dcat:theme` | `superclass_iris` (deduped) |
| `rdfs:subPropertyOf` | `superproperty_iris` |
| `rdf:type` | `node_kind` (`concept` \| `class` \| `property`) |

**Children:** `children_iris` wired from broader/subClassOf/subPropertyOf inverse
map after extract.

**Formats:** explicit suffix → rdflib format map; unknown suffix → rdflib sniff.

### Ingest (`ingest.seed_taxonomy`)

Calls `load_taxonomy_nodes(settings)`; honours `SCUDO_TAXONOMY_SEED` override.

---

## 6. WS3 — `TaxonomyNode` extension

```python
class TaxonomyNode(BaseModel):
    iri: str
    label: str
    parent_iri: Optional[str] = None
    children_iris: list[str] = []
    definition: str = ""
    alt_labels: list[str] = []
    node_kind: Literal["concept", "class", "property"] = "concept"
    superclass_iris: list[str] = []
    superproperty_iris: list[str] = []
```

Existing callers that only set `iri` / `label` / `parent_iri` remain valid.

---

## 7. WS7 (P0) — `taxonomy_text.py`

| Helper | When `SCUDO_TAXONOMY_TEXT` off | When on |
|--------|-------------------------------|---------|
| `taxonomy_candidate_desc` | `""` | `node.definition` |
| `taxonomy_dense_text` | `label` | `label` + definition |
| `taxonomy_bm25_doc` | `label` | label + alt_labels + definition |

**Threading (P0):**

- `retrieval._bm25_prefilter` → `taxonomy_bm25_doc` for BM25 docs.
- `FalkorDBStore` / `MemoryStore` legacy path → dense + BM25 helpers.
- `NeptuneStore` → persist/read SKOS fields; placeholder retrieval unchanged.

Flag defaults preserve pre-ontology matching behaviour.

---

## 8. Configuration

| Env var | `Settings` field | Default | Notes |
|---------|------------------|---------|-------|
| `SCUDO_TAXONOMY_LOADER` | `taxonomy_loader` | `cdao` | `cdao` \| `dcat` |
| `SCUDO_TAXONOMY_SOURCE` | `taxonomy_source` | `""` | Required when loader=`dcat` |
| `SCUDO_TAXONOMY_TEXT` | `taxonomy_text_enabled` | off | truthy: `1/true/yes/on` |
| `SCUDO_DENSE_BACKEND` | `dense_backend` | `jaro_winkler` | validated in `Settings.from_env` |
| `SCUDO_USE_OPUS_DENSE` | `use_opus_dense` | off | unchanged; multi-path delegation pre-existing on FalkorDB |

Live env readers (`env_dense_backend`, `env_use_opus_dense`, `env_taxonomy_text_enabled`)
re-read os.environ for tests/smoke without mutating the module singleton.

---

## 9. Store persistence (P0)

### FalkorDB

- Upsert `definition`, `alt_labels`, `node_kind` on `TaxonomyNode`.
- `SUBCLASS_OF` / `SUBPROPERTY_OF` edges from `superclass_iris` / `superproperty_iris`.
- Drop stale `HAS_CHILD` / subsumption edges before re-wire on re-seed.

### Neptune

- SPARQL read/write for SKOS text + subsumption fields on `skos:Concept` / anchor
  predicates (see `neptune_store.py` upsert/get/list).

### Memory

- Inherits `FakeStore` writes; scoring path uses `taxonomy_*` helpers on legacy
  Jaro+BM25+RRF composition.

---

## 10. Dependencies

`backend/requirements.txt` (P0):

- **Add:** `rdflib>=7.0.0` (WS1 only).
- **Keep:** `urllib3>=2.0`.
- **Do not add:** `pyshacl` (WS5 / P2).

---

## 11. Residual evidence gaps (verified read-only for P0)

### 11.1 `matching.py` band/merge (spec §11 — lines 276–278 in original draft)

- `SpecialistScorer` is per-call DI: `(ref, candidates) -> Optional[Candidate]`.
- Borderline concurrence: `confidence = min(best.similarity, specialist_pick.similarity)`.
- Bands from `_gate_thresholds()` / `pass_threshold()` / `borderline_threshold()`.
- **P0:** do not edit `matching.py`; text injection stops at store/retrieval.

### 11.2 `enrichment.py` rich-model shapes (§279–281)

- Uses `ConceptualGraph` / `ConceptualNode` / `ConceptualEdge` from `models.py`.
- Metadata-only; never feeds matcher (I5).
- **P0:** no DCAT projection into enrichment.

### 11.3 Skill / tool contract (§282–284)

| Skill | Tools | Backend |
|-------|-------|---------|
| taxonomy-mapping | `graphrag_retrieve`, `neptune_*`, `rdf_serialise_mapping` | `scudo/tools.py` |
| rdf-serialisation | `rdf_serialise_*`, `rdf_validate_shapes` | `scudo/rdf/fake.py` (P2 swap) |

**P0:** do not modify `scudo/tools.py` unless a P0 test proves required.

### 11.4 Neptune precedent schema (§285–287)

- Positive: `VendorProduct -[mds:mappedTo]-> mds:PrecedentEdge -[mds:target]-> skos:Concept`.
- Confirmed edges filter `!provisional`.
- **P0:** no precedent schema changes; P1 calibration may use `list_confirmed_precedents`.

---

## 12. P0 acceptance

### Env activation

```bash
export SCUDO_TAXONOMY_LOADER=dcat
export SCUDO_TAXONOMY_SOURCE=/path/to/ontology.ttl   # optional: SCUDO_TAXONOMY_TEXT=on
```

### Import smoke

```bash
PYTHONPATH=backend python3 -c \
  "import scudo_mapping_mcp.retrieval; import scudo_mapping_mcp.store.memory_store; import scudo.tools"
```

### Focused pytest

```bash
PYTHONPATH=backend python3 -m pytest \
  backend/scudo_mapping_mcp/tests/test_import_smoke.py \
  backend/scudo_mapping_mcp/tests/test_ontology_loader.py \
  backend/scudo_mapping_mcp/tests/test_dcat_loader.py \
  backend/scudo_mapping_mcp/tests/test_taxonomy_text_threading.py -q
```

### Fixtures

- `tests/fixtures/dcat_taxonomy.ttl`, `dcat_taxonomy.jsonld`, `p0_ontology_minimal.ttl`

---

## 13. Explicitly out of scope (reverted / not started)

- `loaders/subsumption.py`, `SCUDO_SUBSUMPTION_*`
- `models_dcat.py`, `rights_odrl.py`, `scudo/rdf/real.py`, SHACL shapes
- `scripts/calibrate_confidence_floor.py`
- Edits to `opus_dense.py`, `specialist.py`, `enrichment.py`, `frames.py`, `smoke.py`
  for P0 sign-off

---

## 14. Open items (post-P0 queue)

| Item | Status |
|------|--------|
| `ARB_B1_agent_host_calls_route_to_match_verify_tier` smoke failure | Pre-existing; not P0 ontology |
| Neptune semantic `find_similar_products` | Placeholder; M9 |
| Full smoke 122-case suite | Run separately; P0 uses focused pytest |
