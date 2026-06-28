"""Generate a SCUDO matching graph fixture for the understand-anything dashboard.

Emits ``fixtures/matching-graph.json`` in the **KnowledgeGraph** contract
(version 1.0.0, kind "codebase") consumed by the :5177 dashboard, plus a
companion ``fixtures/meta.json`` with provenance metadata.

The legacy ``build_match_payload()`` function is retained for API use.

Run from backend/:  python -m scudo.build_matching_graph
"""

from __future__ import annotations

import datetime
import json
import os
from typing import Any

os.environ.setdefault("STORE_BACKEND", "memory")
os.environ.setdefault("FRAME_SOURCE", "mock")

_HERE = os.path.dirname(os.path.abspath(__file__))
_CATALOGUE = os.path.join(_HERE, "fixtures", "cdao_catalogue.json")
_OUT = os.path.join(_HERE, "fixtures", "matching-graph.json")
_META_OUT = os.path.join(_HERE, "fixtures", "meta.json")

from scudo_mapping_mcp.config import (  # noqa: E402
    borderline_threshold,
    pass_threshold,
)

PASS_THRESHOLD = pass_threshold()  # 0.85 (rounded — avoids 0.8500000000000001)
BORDERLINE_THRESHOLD = borderline_threshold()  # 0.75
_PASS = f"{PASS_THRESHOLD:.2f}"
_BORDER = f"{BORDERLINE_THRESHOLD:.2f}"

_SAMPLES = [
    {
        "vendor": "LSEG",
        "vendor_slug": "lseg",
        "product_id": "LSEG-EQ-PX",
        "name": "Global Equity Prices",
        "description": "End-of-day and intraday equity price observations",
        "display_vendor": "LSEG",
    },
    {
        "vendor": "S&P Global",
        "vendor_slug": "spglobal",
        "product_id": "SPG-FX",
        "name": "FX Spot and Forward Rates",
        "description": "Foreign exchange spot and forward rates",
        "display_vendor": "SPGLOBAL",
    },
    {
        "vendor": "ICE",
        "vendor_slug": "ice",
        "product_id": "ICE-CA",
        "name": "Corporate Actions Feed",
        "description": "Dividends, splits, mergers and other corporate events",
        "display_vendor": "ICE",
    },
]

# Pipeline stages for the legacy MatchPayload path — kept for API compatibility.
_PIPELINE_STAGES: list[tuple[str, str, str]] = [
    (
        "stage:vendor-catalog",
        "Vendor Catalog",
        "Vendor product arrives from S3 upload — the starting point of the matching story.",
    ),
    (
        "stage:parse-normalize",
        "(A) Parse & Normalize",
        "Deterministic parse and rule engine — cheapest rung; exact matches settle here.",
    ),
    (
        "stage:semantic-match",
        "(B) Semantic Matching",
        "Titan embeddings + BM25 + Neptune structural fusion via RRF retrieval.",
    ),
    (
        "stage:rank-score",
        "(C) Rank & Score",
        "Fuse top candidates; produce similarity scores for the confidence gate.",
    ),
    (
        "stage:confidence-gate",
        "Confidence Gate",
        f"Three bands: PASS ≥{PASS_THRESHOLD:.2f}, BORDERLINE {BORDERLINE_THRESHOLD:.2f}–{PASS_THRESHOLD:.2f}, FAIL <{BORDERLINE_THRESHOLD:.2f}.",
    ),
    (
        "stage:route-pass",
        "Aurora Audit + JAPI Persist",
        "PASS band — write audit log and fan out to Aurora, Neptune, OpenSearch.",
    ),
    (
        "stage:route-borderline",
        "Orchestration (Strands)",
        "BORDERLINE band — Strands + Bedrock Specialist and Verifier agents.",
    ),
    (
        "stage:route-fail",
        "Human Review (Scudo UI)",
        "FAIL band — data steward adjudicates via HITL; decision feeds precedents.",
    ),
    (
        "stage:japi-persist",
        "JAPI Persist",
        "Transactional outbox — right store for the right job after confirmed match.",
    ),
]

_FLOW_EDGES: list[tuple[str, str]] = [
    ("stage:vendor-catalog", "stage:parse-normalize"),
    ("stage:parse-normalize", "stage:semantic-match"),
    ("stage:semantic-match", "stage:rank-score"),
    ("stage:rank-score", "stage:confidence-gate"),
    ("stage:confidence-gate", "stage:route-pass"),
    ("stage:confidence-gate", "stage:route-borderline"),
    ("stage:confidence-gate", "stage:route-fail"),
    ("stage:route-pass", "stage:japi-persist"),
    ("stage:route-borderline", "stage:japi-persist"),
    ("stage:route-fail", "stage:japi-persist"),
]


def _band_from_score(score: float) -> str:
    if score >= PASS_THRESHOLD:
        return "pass"
    if score >= BORDERLINE_THRESHOLD:
        return "borderline"
    return "fail"


def _cdao_kind(iri: str) -> str:
    if ":domain:" in iri:
        return "cdao-domain"
    if ":subdomain:" in iri:
        return "cdao-subdomain"
    return "cdao-concept"


def _load_catalogue_meta() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    from scudo.seed_falkordb import _load_fixture

    raw_nodes = _load_fixture(_CATALOGUE)
    by_iri: dict[str, dict[str, Any]] = {}
    with open(_CATALOGUE, encoding="utf-8") as fh:
        doc = json.load(fh)
    graph = doc.get("@graph") or doc.get("nodes") or []
    caption_map: dict[str, str] = {}
    for entry in graph:
        iri = entry.get("@id") or entry.get("iri")
        if iri and entry.get("caption"):
            caption_map[str(iri)] = str(entry["caption"])
    for n in raw_nodes:
        iri = n["iri"]
        by_iri[iri] = {
            **n,
            "caption": caption_map.get(iri, f"CDAO node: {n['label']}"),
            "provenance": "synthetic",
        }
    return raw_nodes, by_iri


def _synthetic_provenance(note: str | None = None) -> dict[str, str]:
    out: dict[str, str] = {"source": "synthetic"}
    if note:
        out["note"] = note
    return out


def _make_node(
    node_id: str,
    label: str,
    kind: str,
    caption: str,
    *,
    provenance: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "label": label,
        "kind": kind,
        "caption": caption[:140],
        "provenance": provenance or _synthetic_provenance(),
    }


def _make_edge(
    source: str,
    target: str,
    kind: str,
    *,
    band: str | None = None,
    weight: float | None = None,
) -> dict[str, Any]:
    edge: dict[str, Any] = {"source": source, "target": target, "kind": kind}
    if band is not None:
        edge["band"] = band
    if weight is not None:
        edge["weight"] = round(float(weight), 4)
    return edge


def build_match_payload(
    vendor: str | None = None,
    ref: str | None = None,
) -> dict[str, Any]:
    """Build the MatchPayload dict. Optional vendor+ref adds a focused drill-down."""
    from scudo.seed_falkordb import _to_taxonomy_node
    from scudo_mapping_mcp import matching
    from scudo_mapping_mcp.models import VendorProductRef
    from scudo_mapping_mcp.store.memory_store import MemoryStore

    # Synthetic graph — always in-memory; must not require FalkorDB/Neptune even
    # when the Flask app was booted with the production STORE_BACKEND default.
    store = MemoryStore()

    catalogue_nodes, catalogue_meta = _load_catalogue_meta()
    for raw in catalogue_nodes:
        store.upsert_taxonomy_node(_to_taxonomy_node(raw))

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_node(node: dict[str, Any]) -> None:
        if node["id"] in seen:
            return
        seen.add(node["id"])
        nodes.append(node)

    for stage_id, label, caption in _PIPELINE_STAGES:
        add_node(_make_node(stage_id, label, "stage", caption))

    for src, tgt in _FLOW_EDGES:
        band = None
        if src == "stage:confidence-gate":
            if tgt == "stage:route-pass":
                band = "pass"
            elif tgt == "stage:route-borderline":
                band = "borderline"
            elif tgt == "stage:route-fail":
                band = "fail"
        edges.append(_make_edge(src, tgt, "flow", band=band))

    for meta in catalogue_meta.values():
        iri = meta["iri"]
        add_node(
            _make_node(
                iri,
                meta["label"],
                _cdao_kind(iri),
                meta["caption"],
            )
        )
        parent = meta.get("parent_iri")
        if parent:
            edges.append(_make_edge(parent, iri, "hierarchy"))

    samples = _SAMPLES
    if vendor and ref:
        samples = [
            s
            for s in _SAMPLES
            if s["vendor"].lower() == vendor.lower() and s["product_id"] == ref
        ] or [
            {
                "vendor": vendor,
                "vendor_slug": vendor.lower(),
                "product_id": ref,
                "name": ref,
                "description": "",
                "display_vendor": vendor.upper(),
            }
        ]

    for vp in samples:
        vref = VendorProductRef(
            vendor=vp["vendor"],
            product_id=vp["product_id"],
            name=vp["name"],
            description=vp.get("description", ""),
            raw={},
        )
        # Canonical mds.<vendor>:<uuid5> IRI (models.mds_iri). The human
        # product ref is preserved in the node caption for drilldown/UI.
        vp_id = vref.iri

        # Use the LOCAL in-memory store for both retrieval and matching so the
        # synthetic payload never touches the production FalkorDB/Neptune even
        # when the app booted with STORE_BACKEND=falkordb. (Passing store= keeps
        # map_vendor_product off the global get_store().)
        cands = store.find_similar_products(vref, max_results=6) or []

        result = matching.map_vendor_product(vref, max_candidates=6, store=store)
        mapped_iri = result.mapped_node_iri

        add_node(
            _make_node(
                vp_id,
                vp["name"],
                "vendor-product",
                f"{vp['vendor']} / {vp['product_id']} — {vp.get('description', '')}".strip(),
            )
        )
        edges.append(_make_edge(vp_id, "stage:vendor-catalog", "flow"))

        for c in cands:
            ciri = c.node.iri
            sim = float(c.similarity or 0.0)
            if ciri not in catalogue_meta and ciri not in seen:
                add_node(
                    _make_node(
                        ciri,
                        c.node.label,
                        _cdao_kind(ciri),
                        catalogue_meta.get(ciri, {}).get("caption", c.node.label),
                    )
                )
            etype = "chosen" if mapped_iri and ciri == mapped_iri else "candidate"
            edges.append(
                _make_edge(
                    "stage:confidence-gate",
                    ciri,
                    etype,
                    band=_band_from_score(sim),
                    weight=sim,
                )
            )

    return {
        "meta": {
            "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "dataProvenance": "synthetic",
            "bands": {
                "pass": PASS_THRESHOLD,
                "borderline": BORDERLINE_THRESHOLD,
            },
        },
        "nodes": nodes,
        "edges": edges,
    }


# ── KnowledgeGraph infrastructure nodes ───────────────────────────────────────

_INFRA_NODES: list[dict[str, Any]] = [
    {
        "id": "etl-eventbridge",
        "type": "pipeline",
        "name": "Amazon EventBridge",
        "summary": "Amazon EventBridge — PutObject/ObjectCreated rule fires on vendor S3 upload.",
        "tags": ["etl", "aws", "eventbridge", "ingestion"],
        "complexity": "simple",
    },
    {
        "id": "etl-sqs",
        "type": "pipeline",
        "name": "SQS Processing Queue",
        "summary": "SQS Processing Queue — decouples S3 event from the Lambda worker; provides retry/DLQ semantics.",
        "tags": ["etl", "aws", "sqs", "queue"],
        "complexity": "simple",
    },
    {
        "id": "etl-lambda",
        "type": "service",
        "name": "Lambda Worker (in VPC)",
        "summary": "AWS Lambda Worker running in VPC — receives SQS messages and orchestrates validate/transform.",
        "tags": ["etl", "aws", "lambda", "compute"],
        "complexity": "moderate",
    },
    {
        "id": "etl-validate",
        "type": "step",
        "name": "Validate / Transform",
        "summary": "Validate / Transform — schema check + field normalisation. FAIL → quarantine; PASS → canonical JSON-LD/Parquet sink + DynamoDB.",
        "tags": ["etl", "validation", "transform"],
        "complexity": "moderate",
    },
    {
        "id": "etl-s3-sink",
        "type": "table",
        "name": "S3 Sink — canonical JSON-LD/Parquet",
        "summary": "S3 Sink — stores canonical JSON-LD and Parquet representations of validated vendor data; feeds the matching engine.",
        "tags": ["etl", "aws", "s3", "storage", "canonical"],
        "complexity": "simple",
    },
    {
        "id": "etl-s3-quarantine",
        "type": "table",
        "name": "S3 Quarantine",
        "summary": "S3 Quarantine — stores failed/malformed records for manual triage. Isolates bad data from the matching pipeline.",
        "tags": ["etl", "aws", "s3", "quarantine", "error"],
        "complexity": "simple",
    },
    {
        "id": "etl-dynamodb",
        "type": "table",
        "name": "DynamoDB — facts + job tracking + version",
        "summary": "DynamoDB — persists extracted facts, job-tracking state, and version metadata for every ingestion event.",
        "tags": ["etl", "aws", "dynamodb", "storage"],
        "complexity": "simple",
    },
    {
        "id": "match-a-parse",
        "type": "step",
        "name": "(A) Parse & Normalize",
        "summary": "(A) Parse & Normalize — 1st-gate exact match + rule engine. Reads canonical vendor form from S3 sink; deterministic rules first (cheapest rungs).",
        "tags": ["matching", "parse", "normalize", "rule-engine"],
        "complexity": "moderate",
    },
    {
        "id": "match-b-semantic",
        "type": "step",
        "name": "(B) Semantic Matching",
        "summary": "(B) Semantic Matching — classification + NLP + Amazon Titan Embeddings. Fuses fuzzy lexical (OpenSearch BM25), k-NN vector (OpenSearch), structural (Neptune RDF), and audit context (Aurora).",
        "tags": ["matching", "semantic", "nlp", "embeddings", "rrf"],
        "complexity": "complex",
    },
    {
        "id": "match-c-rank",
        "type": "step",
        "name": "(C) Rank & Score",
        "summary": "(C) Rank & Score — fuse top-3 candidates via RRF + confidence scoring. Produces a ranked list with similarity scores for the confidence gate.",
        "tags": ["matching", "ranking", "scoring", "rrf"],
        "complexity": "moderate",
    },
    {
        "id": "match-confidence-gate",
        "type": "flow",
        "name": "Confidence Gate",
        "summary": (
            f"Confidence Gate — {_PASS} pass threshold. Three routing bands: "
            f">={_PASS} PASS → Aurora Audit Log + JAPI Persist; "
            f"{_BORDER}–{_PASS} BORDERLINE → Orchestration Layer (Strands); "
            f"<{_BORDER} FAIL → Human Review (Scudo UI)."
        ),
        "tags": ["matching", "gate", "confidence", "routing"],
        "complexity": "moderate",
    },
    {
        "id": "orch-strands",
        "type": "service",
        "name": "Strands Orchestrator",
        "summary": (
            f"Strands Orchestrator — deterministic routing with Claude Opus 4.8. "
            f"Activated only for BORDERLINE confidence ({_BORDER}–{_PASS}). "
            f"Coordinates the Specialist, Verifier and AgentCore Memory agents."
        ),
        "tags": ["orchestration", "strands", "bedrock", "agentic", "claude"],
        "complexity": "complex",
    },
    {
        "id": "orch-agent-memory",
        "type": "service",
        "name": "AgentCore Memory",
        "summary": "AgentCore Memory — message orchestration and memory plane. Supplies conversational context and prior decisions to the Strands Orchestrator.",
        "tags": ["orchestration", "memory", "bedrock", "agentcore"],
        "complexity": "moderate",
    },
    {
        "id": "orch-specialist",
        "type": "service",
        "name": "Specialist (Bedrock)",
        "summary": "Specialist · Bedrock — proposes target CDAO node, supporting evidence, and RDF triples. Runs on borderline candidates only.",
        "tags": ["orchestration", "bedrock", "specialist", "llm"],
        "complexity": "complex",
    },
    {
        "id": "orch-verifier",
        "type": "service",
        "name": "Verifier (Bedrock)",
        "summary": "Verifier · Bedrock — applies a 10-dimension rubric. Score >=10 PASS; <10 FAIL → escalate to Human Review.",
        "tags": ["orchestration", "bedrock", "verifier", "rubric"],
        "complexity": "complex",
    },
    {
        "id": "orch-evaluations",
        "type": "service",
        "name": "Bedrock Evaluations",
        "summary": "Bedrock Evaluations — captures traces, token counts, and context-rot signals for observability and quality improvement.",
        "tags": ["orchestration", "bedrock", "evaluation", "observability"],
        "complexity": "moderate",
    },
    {
        "id": "hitl-review",
        "type": "entity",
        "name": "Human Review — Scudo UI",
        "summary": (
            f"Human Review — Data Steward adjudication via Scudo UI. "
            f"Receives FAIL (<{_BORDER}) and uncertain Verifier outcomes. "
            f"Decision feeds back into Aurora + Neptune."
        ),
        "tags": ["hitl", "human-review", "data-steward", "scudo-ui"],
        "complexity": "moderate",
    },
    {
        "id": "store-aurora-audit",
        "type": "table",
        "name": "Aurora — Audit Log",
        "summary": "Aurora — Audit Log. Written immediately on every PASS decision from the confidence gate; also written after HITL decisions.",
        "tags": ["storage", "aurora", "audit", "rds"],
        "complexity": "simple",
    },
    {
        "id": "store-aurora-catalog",
        "type": "table",
        "name": "Aurora — Catalogue + CDAO Taxonomy + Lineage",
        "summary": "Aurora — canonical catalogue, CDAO taxonomy, and lineage graph. Source of truth for JAPI persist; drives Neptune and OpenSearch indexes.",
        "tags": ["storage", "aurora", "catalogue", "taxonomy", "lineage", "rds"],
        "complexity": "moderate",
    },
    {
        "id": "store-neptune",
        "type": "table",
        "name": "Neptune — Canonical RDF + ODRL",
        "summary": "Neptune — stores the Canonical RDF graph and ODRL rights expressions (SPARQL). CDAO Catalog Data is indexed here via Scudo. Read by Semantic Matching; written by JAPI Persist.",
        "tags": ["storage", "neptune", "rdf", "sparql", "odrl", "graph-db"],
        "complexity": "moderate",
    },
    {
        "id": "store-opensearch",
        "type": "table",
        "name": "OpenSearch — lexical + k-NN index",
        "summary": "OpenSearch — dual-mode index: BM25 fuzzy lexical search and k-NN vector search. CDAO Catalog Data is indexed here; read by Semantic Matching; refreshed by JAPI Persist.",
        "tags": ["storage", "opensearch", "vector", "lexical", "knn", "bm25"],
        "complexity": "moderate",
    },
    {
        "id": "titan-embeddings",
        "type": "service",
        "name": "Amazon Titan Embeddings",
        "summary": "Amazon Titan Embeddings — generates dense vector representations of vendor product descriptions and CDAO labels for k-NN retrieval.",
        "tags": ["aws", "bedrock", "titan", "embeddings", "vector"],
        "complexity": "simple",
    },
    {
        "id": "cdao-catalog-data",
        "type": "entity",
        "name": "CDAO Catalog Data",
        "summary": "CDAO Catalog Data — authoritative taxonomy maintained by the CDAO; indexed via Scudo into Neptune (RDF) and OpenSearch (vector + lexical).",
        "tags": ["cdao", "catalog", "taxonomy", "reference"],
        "complexity": "simple",
    },
    {
        "id": "transactional-outbox",
        "type": "pipeline",
        "name": "Transactional Outbox (EventBridge/SQS)",
        "summary": "Transactional Outbox — EventBridge + SQS pattern that fans out a confirmed match decision to Aurora, Neptune, and OpenSearch atomically.",
        "tags": ["japi", "eventbridge", "sqs", "outbox", "pattern"],
        "complexity": "moderate",
    },
    {
        "id": "appsync-publish",
        "type": "service",
        "name": "AppSync / WebSockets — publish to Scudo",
        "summary": "AppSync / WebSockets — real-time push to the Scudo UI after HITL approval; notifies downstream subscribers that a match is confirmed.",
        "tags": ["japi", "aws", "appsync", "websockets", "publish"],
        "complexity": "simple",
    },
]

_INFRA_EDGES: list[dict[str, Any]] = [
    {
        "source": "etl-eventbridge",
        "target": "etl-sqs",
        "type": "triggers",
        "direction": "forward",
        "weight": 1.0,
    },
    {
        "source": "etl-sqs",
        "target": "etl-lambda",
        "type": "triggers",
        "direction": "forward",
        "weight": 1.0,
    },
    {
        "source": "etl-lambda",
        "target": "etl-validate",
        "type": "flow_step",
        "direction": "forward",
        "weight": 1.0,
    },
    {
        "source": "etl-validate",
        "target": "etl-s3-quarantine",
        "type": "writes_to",
        "direction": "forward",
        "weight": 0.3,
        "description": "on FAIL",
    },
    {
        "source": "etl-validate",
        "target": "etl-s3-sink",
        "type": "writes_to",
        "direction": "forward",
        "weight": 0.7,
        "description": "on PASS",
    },
    {
        "source": "etl-validate",
        "target": "etl-dynamodb",
        "type": "writes_to",
        "direction": "forward",
        "weight": 0.7,
    },
    {
        "source": "etl-s3-sink",
        "target": "match-a-parse",
        "type": "triggers",
        "direction": "forward",
        "weight": 1.0,
    },
    {
        "source": "match-a-parse",
        "target": "match-b-semantic",
        "type": "flow_step",
        "direction": "forward",
        "weight": 1.0,
    },
    {
        "source": "match-b-semantic",
        "target": "match-c-rank",
        "type": "flow_step",
        "direction": "forward",
        "weight": 1.0,
    },
    {
        "source": "match-c-rank",
        "target": "match-confidence-gate",
        "type": "flow_step",
        "direction": "forward",
        "weight": 1.0,
    },
    {
        "source": "store-aurora-catalog",
        "target": "match-b-semantic",
        "type": "reads_from",
        "direction": "forward",
        "weight": 0.9,
    },
    {
        "source": "store-neptune",
        "target": "match-b-semantic",
        "type": "reads_from",
        "direction": "forward",
        "weight": 0.9,
    },
    {
        "source": "store-opensearch",
        "target": "match-b-semantic",
        "type": "reads_from",
        "direction": "forward",
        "weight": 0.9,
    },
    {
        "source": "titan-embeddings",
        "target": "match-b-semantic",
        "type": "flow_step",
        "direction": "forward",
        "weight": 0.8,
    },
    {
        "source": "cdao-catalog-data",
        "target": "store-neptune",
        "type": "writes_to",
        "direction": "forward",
        "weight": 1.0,
    },
    {
        "source": "cdao-catalog-data",
        "target": "store-opensearch",
        "type": "writes_to",
        "direction": "forward",
        "weight": 1.0,
    },
    {
        "source": "orch-strands",
        "target": "orch-specialist",
        "type": "triggers",
        "direction": "forward",
        "weight": 1.0,
    },
    {
        "source": "orch-specialist",
        "target": "orch-verifier",
        "type": "flow_step",
        "direction": "forward",
        "weight": 1.0,
    },
    {
        "source": "orch-strands",
        "target": "orch-agent-memory",
        "type": "reads_from",
        "direction": "forward",
        "weight": 0.8,
    },
    {
        "source": "orch-strands",
        "target": "orch-evaluations",
        "type": "triggers",
        "direction": "forward",
        "weight": 0.7,
    },
    {
        "source": "orch-verifier",
        "target": "hitl-review",
        "type": "triggers",
        "direction": "forward",
        "weight": 0.4,
        "description": "uncertain — rubric score <10",
    },
    {
        "source": "hitl-review",
        "target": "store-aurora-audit",
        "type": "writes_to",
        "direction": "forward",
        "weight": 1.0,
    },
    {
        "source": "hitl-review",
        "target": "transactional-outbox",
        "type": "triggers",
        "direction": "forward",
        "weight": 1.0,
    },
    {
        "source": "hitl-review",
        "target": "appsync-publish",
        "type": "triggers",
        "direction": "forward",
        "weight": 1.0,
    },
    {
        "source": "transactional-outbox",
        "target": "store-aurora-catalog",
        "type": "writes_to",
        "direction": "forward",
        "weight": 1.0,
    },
    {
        "source": "store-aurora-catalog",
        "target": "store-neptune",
        "type": "flow_step",
        "direction": "forward",
        "weight": 1.0,
    },
    {
        "source": "store-neptune",
        "target": "store-opensearch",
        "type": "flow_step",
        "direction": "forward",
        "weight": 0.8,
    },
    {
        "source": "match-confidence-gate",
        "target": "store-aurora-audit",
        "type": "writes_to",
        "direction": "forward",
        "weight": 1.0,
        "description": f"PASS >={_PASS}",
        "data": {"band": "pass"},
    },
    {
        "source": "match-confidence-gate",
        "target": "orch-strands",
        "type": "triggers",
        "direction": "forward",
        "weight": 0.5,
        "description": f"BORDERLINE {_BORDER}–{_PASS}",
        "data": {"band": "borderline"},
    },
    {
        "source": "match-confidence-gate",
        "target": "hitl-review",
        "type": "triggers",
        "direction": "forward",
        "weight": 0.3,
        "description": f"FAIL <{_BORDER}",
        "data": {"band": "fail"},
    },
]

# Canonical CDAO taxonomy node ids (jpmorgan:data:cdao:* IRIs from catalogue).
_CDAO_MARKET_DATA_IDS = [
    "jpmorgan:data:cdao:domain:market-data",
    "jpmorgan:data:cdao:subdomain:pricing",
    "jpmorgan:data:cdao:concept:equity-prices",
    "jpmorgan:data:cdao:concept:fixed-income-prices",
    "jpmorgan:data:cdao:concept:fx-rates",
    "jpmorgan:data:cdao:subdomain:indices",
    "jpmorgan:data:cdao:concept:benchmark-index",
]
_CDAO_REFERENCE_DATA_IDS = [
    "jpmorgan:data:cdao:domain:reference-data",
    "jpmorgan:data:cdao:subdomain:instruments",
    "jpmorgan:data:cdao:concept:security-master",
    "jpmorgan:data:cdao:concept:corporate-actions",
    "jpmorgan:data:cdao:subdomain:entities",
    "jpmorgan:data:cdao:concept:legal-entity",
    "jpmorgan:data:cdao:concept:counterparty",
]


def _cdao_node_type(iri: str) -> str:
    if ":domain:" in iri:
        return "domain"
    if ":subdomain:" in iri:
        return "resource"
    return "concept"


def _cdao_node_tags(iri: str) -> list[str]:
    tags = ["cdao"]
    if ":domain:" in iri:
        tags.append("domain")
    elif ":subdomain:" in iri:
        tags.append("subdomain")
    else:
        tags.append("concept")
    return tags


def build_knowledge_graph() -> dict[str, Any]:
    """Build the KnowledgeGraph dict (understand-anything dashboard contract).

    Emits version 1.0.0 / kind "codebase" with jpmorgan:data:cdao:* IRIs,
    no Marketing domain, and confidence bands from config (0.85/0.75).
    """
    from scudo.seed_falkordb import _load_fixture, _to_taxonomy_node
    from scudo_mapping_mcp.models import VendorProductRef
    from scudo_mapping_mcp.store import get_store, reset_store_cache

    reset_store_cache()
    store = get_store()

    # Load and seed catalogue
    catalogue_raw = _load_fixture(_CATALOGUE)
    with open(_CATALOGUE, encoding="utf-8") as fh:
        catalogue_doc = json.load(fh)
    graph_entries = catalogue_doc.get("@graph") or []
    cdao_caption: dict[str, str] = {}
    for entry in graph_entries:
        iri = str(entry.get("@id") or entry.get("iri") or "")
        if iri and entry.get("caption"):
            cdao_caption[iri] = str(entry["caption"])

    for raw in catalogue_raw:
        store.upsert_taxonomy_node(_to_taxonomy_node(raw))

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen_nodes: set[str] = set()

    def add_node(n: dict[str, Any]) -> None:
        if n["id"] in seen_nodes:
            return
        seen_nodes.add(n["id"])
        nodes.append(n)

    # Infrastructure nodes
    for n in _INFRA_NODES:
        add_node(n)

    # Infrastructure edges
    edges.extend(_INFRA_EDGES)

    # CDAO taxonomy nodes and hierarchy edges
    for raw in catalogue_raw:
        iri = raw["iri"]
        label = raw["label"]
        caption = cdao_caption.get(iri, f"{label} — CDAO {_cdao_node_type(iri)}")
        add_node(
            {
                "id": iri,
                "type": _cdao_node_type(iri),
                "name": label,
                "summary": caption,
                "tags": _cdao_node_tags(iri),
                "complexity": "simple",
            }
        )
        parent = raw.get("parent_iri")
        if parent:
            edges.append(
                {
                    "source": parent,
                    "target": iri,
                    "type": "contains",
                    "direction": "forward",
                    "weight": 1.0,
                }
            )

    # Vendor product nodes — run real matcher to get candidate edges
    vendor_ids: list[str] = []
    vendor_tour_steps: list[dict[str, Any]] = []

    for vp in _SAMPLES:
        vref = VendorProductRef(
            vendor=vp["vendor"],
            product_id=vp["product_id"],
            name=vp["name"],
            description=vp.get("description", ""),
            raw={},
        )
        # Canonical mds.<vendor>:<uuid5> IRI — runtime source of truth
        # (models.mds_iri). The human ref lives in name/summary/data.
        vp_id = vref.iri
        vendor_ids.append(vp_id)

        try:
            cands = store.find_similar_products(vref, max_results=6) or []
        except Exception:  # noqa: BLE001
            cands = []

        add_node(
            {
                "id": vp_id,
                "type": "entity",
                "name": vp["name"],
                "summary": f"{vp['vendor']} / {vp['product_id']} — {vp.get('description', '')}".rstrip(
                    " —"
                ).strip(),
                "tags": ["vendor", vp["vendor_slug"]],
                "complexity": "moderate",
            }
        )

        edges.append(
            {
                "source": vp_id,
                "target": "etl-eventbridge",
                "type": "triggers",
                "direction": "forward",
                "weight": 1.0,
                "description": "vendor S3 upload triggers ingestion",
            }
        )

        top_cdao_ids: list[str] = []
        for c in cands:
            ciri = c.node.iri
            sim = float(c.similarity or 0.0)
            edges.append(
                {
                    "source": "match-confidence-gate",
                    "target": ciri,
                    "type": "similar_to",
                    "direction": "forward",
                    "weight": round(sim, 4),
                    "description": f"product={vp_id}; similarity={sim:.4f}; candidate",
                    "data": {"band": _band_from_score(sim), "weight": round(sim, 4)},
                }
            )
            top_cdao_ids.append(ciri)

        tour_node_ids = [
            vp_id,
            "etl-eventbridge",
            "match-a-parse",
            "match-b-semantic",
            "match-c-rank",
            "match-confidence-gate",
        ] + top_cdao_ids[:3]

        vendor_tour_steps.append(
            {
                "title": f"Match: {vp['name']} ({vp['display_vendor']})",
                "description": (
                    f"{vp['vendor']} product '{vp['name']}' ({vp['product_id']}) "
                    f"ingested via EventBridge → ETL → matching engine. "
                    f"Retrieved {len(cands)} CDAO candidate(s)."
                ),
                "nodeIds": tour_node_ids,
            }
        )

    # Layers
    layers: list[dict[str, Any]] = [
        {
            "id": "layer:etl",
            "name": "ETL Ingestion Pipeline",
            "description": "Amazon EventBridge → SQS → Lambda → validate/transform → S3 sink / quarantine + DynamoDB.",
            "nodeIds": [
                "etl-eventbridge",
                "etl-sqs",
                "etl-lambda",
                "etl-validate",
                "etl-s3-sink",
                "etl-s3-quarantine",
                "etl-dynamodb",
            ],
        },
        {
            "id": "layer:matching-engine",
            "name": "Matching Engine (AIA)",
            "description": f"Three-stage matching pipeline: (A) parse+normalize, (B) semantic matching with Titan Embeddings, (C) rank+score → confidence gate (bands {_BORDER}/{_PASS}).",
            "nodeIds": [
                "match-a-parse",
                "match-b-semantic",
                "match-c-rank",
                "match-confidence-gate",
                "titan-embeddings",
            ],
        },
        {
            "id": "layer:orchestration",
            "name": "Orchestration Layer (Borderline only)",
            "description": f"Strands Orchestrator with Claude Opus 4.8; activated only for the {_BORDER}–{_PASS} BORDERLINE band. Specialist + Verifier + Evaluations.",
            "nodeIds": [
                "orch-strands",
                "orch-agent-memory",
                "orch-specialist",
                "orch-verifier",
                "orch-evaluations",
            ],
        },
        {
            "id": "layer:japi-persist",
            "name": "JAPI Persist — Right Store for the Right Job",
            "description": "Transactional outbox → Aurora → Neptune RDF + ODRL → OpenSearch. HITL adjudication + AppSync real-time publish.",
            "nodeIds": [
                "store-aurora-audit",
                "store-aurora-catalog",
                "store-neptune",
                "store-opensearch",
                "cdao-catalog-data",
                "transactional-outbox",
                "appsync-publish",
                "hitl-review",
            ],
        },
        {
            "id": "layer:cdao-market-data",
            "name": "CDAO — Market Data",
            "description": "CDAO Market Data domain: Pricing and Indices subdomains with leaf concepts managed by the Chief Data and Analytics Office.",
            "nodeIds": _CDAO_MARKET_DATA_IDS,
        },
        {
            "id": "layer:cdao-reference-data",
            "name": "CDAO — Reference Data",
            "description": "CDAO Reference Data domain: Instrument and Entity reference subdomains with leaf concepts managed by the Chief Data and Analytics Office.",
            "nodeIds": _CDAO_REFERENCE_DATA_IDS,
        },
        {
            "id": "layer:vendor-products",
            "name": "Vendor Products",
            "description": "Sample vendor data products from LSEG, S&P Global, and ICE that are exercised through the SCUDO matching pipeline.",
            "nodeIds": vendor_ids,
        },
    ]

    # Tour
    tour: list[dict[str, Any]] = [
        {
            "order": 1,
            "title": "ETL Ingestion Flow",
            "description": (
                "Vendor data arrives via S3 upload. EventBridge fires a PutObject rule "
                "that queues the event in SQS. The Lambda Worker (in VPC) dequeues, "
                "validates, and transforms the record. Valid records land in the S3 "
                "canonical sink (JSON-LD/Parquet) and DynamoDB; invalid records are "
                "routed to the S3 quarantine bucket."
            ),
            "nodeIds": [
                "etl-eventbridge",
                "etl-sqs",
                "etl-lambda",
                "etl-validate",
                "etl-s3-sink",
                "etl-dynamodb",
            ],
        },
    ]
    for i, step in enumerate(vendor_tour_steps):
        tour.append({"order": i + 2, **step})

    tour.append(
        {
            "order": len(vendor_tour_steps) + 2,
            "title": "Borderline Orchestration (Strands + Bedrock)",
            "description": (
                f"When the confidence gate lands in the {_BORDER}–{_PASS} "
                f"BORDERLINE band, the Strands Orchestrator (Claude Opus 4.8) is invoked. "
                f"It reads from AgentCore Memory, dispatches a Specialist (Bedrock) to propose "
                f"the target CDAO node and triples, and runs a Verifier (Bedrock) against a "
                f"10-dimension rubric. Score >=10 promotes to AUTO_MAPPED; <10 escalates to "
                f"Human Review. Bedrock Evaluations captures all traces."
            ),
            "nodeIds": [
                "match-confidence-gate",
                "orch-strands",
                "orch-agent-memory",
                "orch-specialist",
                "orch-verifier",
                "orch-evaluations",
                "hitl-review",
            ],
        }
    )
    tour.append(
        {
            "order": len(vendor_tour_steps) + 3,
            "title": "JAPI Persist — Right Store for the Right Job",
            "description": (
                "After a confirmed decision (PASS gate or HITL approval), the Transactional "
                "Outbox (EventBridge/SQS) fans out atomically to: Aurora (catalogue + CDAO "
                "taxonomy + lineage), Neptune (Canonical RDF + ODRL rights via SPARQL), and "
                "OpenSearch (lexical + k-NN index refresh). HITL-approved matches are published "
                "in real time to the Scudo UI via AppSync/WebSockets."
            ),
            "nodeIds": [
                "hitl-review",
                "transactional-outbox",
                "store-aurora-catalog",
                "store-neptune",
                "store-opensearch",
                "appsync-publish",
            ],
        }
    )

    return {
        "version": "1.0.0",
        "kind": "codebase",
        "project": {
            "name": "SCUDO Matching Comprehension",
            "description": (
                "End-to-end SCUDO architecture graph: ETL ingestion (EventBridge → SQS → Lambda → S3/DynamoDB), "
                "three-stage matching engine (parse, semantic, rank), confidence gate with three routing bands "
                f"(PASS ≥{_PASS} / BORDERLINE {_BORDER}–{_PASS} / FAIL <{_BORDER}), "
                "agentic orchestration (Strands + Bedrock Specialist + Verifier), HITL adjudication, and JAPI persist "
                "(Aurora → Neptune → OpenSearch). Vendor product nodes carry real matcher confidence scores; CDAO taxonomy "
                "is loaded from the fixtures/cdao_catalogue.json fixture. ILLUSTRATIVE — synthetic data, not authoritative."
            ),
            "languages": ["Python"],
            "frameworks": [
                "Amazon Bedrock",
                "Strands Orchestrator",
                "Amazon Titan Embeddings",
                "Amazon Neptune",
                "Amazon Aurora",
                "Amazon OpenSearch",
                "Amazon DynamoDB",
                "AWS Lambda",
                "Amazon SQS",
                "Amazon EventBridge",
                "AWS AppSync",
                "scudo-cost-ladder",
            ],
            "analyzedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "gitCommitHash": "offline-fixture",
        },
        "nodes": nodes,
        "edges": edges,
        "layers": layers,
        "tour": tour,
    }


def main() -> int:
    kg = build_knowledge_graph()
    with open(_OUT, "w", encoding="utf-8") as fh:
        json.dump(kg, fh, indent=2)
    print(
        f"[matching-graph] wrote {_OUT}: "
        f"{len(kg['nodes'])} nodes, {len(kg['edges'])} edges"
    )

    meta = {
        "dataProvenance": "synthetic",
        "bands": {
            "pass": round(PASS_THRESHOLD, 2),
            "borderline": round(BORDERLINE_THRESHOLD, 2),
        },
    }
    with open(_META_OUT, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)
    print(f"[matching-graph] wrote {_META_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
