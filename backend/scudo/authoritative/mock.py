"""In-memory authoritative-graph mock for local smoke tests.

Contains: a small CDAO taxonomy, hand-curated prior mappings, and a
conflict fixture. Shape matches what the Neptune MCP openCypher templates
produce in real mode so the orchestrator is insulated from the mode switch.
"""
from __future__ import annotations

from typing import Optional


# Small CDAO-shaped taxonomy. Real CDAO has thousands of nodes; this is
# enough to make the authoritative-confirmation path testable.
_CDAO_NODES = [
    {
        "iri": "jpmorgan:data:cdao:EquityResearch",
        "label": "EquityResearch",
        "definition": "Sell-side and independent analyst research on listed equities.",
        "synonyms": ["equity research", "sell-side research", "stock research", "estimates"],
    },
    {
        "iri": "jpmorgan:data:cdao:InvestmentData",
        "label": "InvestmentData",
        "definition": "Curated investment-decision-relevant data products.",
        "synonyms": ["investment data", "investment information"],
    },
    {
        "iri": "jpmorgan:data:cdao:Fundamentals",
        "label": "Fundamentals",
        "definition": "Company-level financial fundamentals.",
        "synonyms": ["fundamentals", "financials", "company financials"],
    },
    {
        "iri": "jpmorgan:data:cdao:Pricing",
        "label": "Pricing",
        "definition": "Market price observations across asset classes.",
        "synonyms": ["pricing", "prices", "eod prices", "market data"],
    },
    {
        "iri": "jpmorgan:data:cdao:CorporateActions",
        "label": "CorporateActions",
        "definition": "Mandatory and voluntary corporate action events.",
        "synonyms": ["corporate actions", "events", "dividends", "splits"],
    },
    {
        "iri": "jpmorgan:data:cdao:ESG",
        "label": "ESG",
        "definition": "Environmental, social, and governance metrics + disclosures.",
        "synonyms": ["esg", "sustainability", "carbon"],
    },
    {
        "iri": "jpmorgan:data:cdao:ReferenceData",
        "label": "ReferenceData",
        "definition": "Static reference data: instruments, entities, symbology.",
        "synonyms": ["reference data", "static data", "symbology"],
    },
    {
        "iri": "jpmorgan:data:cdao:FixedIncome",
        "label": "FixedIncome",
        "definition": "Fixed-income securities — bonds, credit, money markets.",
        "synonyms": ["fixed income", "bonds", "credit"],
    },
    {
        "iri": "jpmorgan:data:cdao:NewsAndResearch",
        "label": "NewsAndResearch",
        "definition": "Newswires and qualitative research artifacts.",
        "synonyms": ["news", "research", "newswire", "transcripts"],
    },
]

_NODE_BY_IRI = {n["iri"]: n for n in _CDAO_NODES}

# Hand-curated prior authoritative mappings.
_PRIOR_MAPPINGS = {
    ("lseg", "LSEG-IBES-EST-001"): {
        "source_iri": "mds.lseg:9e420bc7-2a1f-5c3d-8e4a-1b2c3d4e5f60",
        "target_iri": "jpmorgan:data:cdao:EquityResearch",
        "rationale": "I/B/E/S consensus estimates align with EquityResearch",
        "confidence": 0.88,
    },
}

# Conflict fixture — equivalent products at OTHER vendors mapped elsewhere.
_CONFLICTS = {
    "LSEG-IBES-EST-001": [
        {
            "other_vendor": "spglobal",
            "other_vendor_product_ref": "SPG-EQRES-77",
            "other_target_iri": "jpmorgan:data:cdao:NewsAndResearch",
            "note": "S&P maps a similar product to NewsAndResearch — possible disagreement",
        },
    ],
}


def node_by_iri(iri: str) -> Optional[dict]:
    n = _NODE_BY_IRI.get(iri)
    if n is None:
        return None
    return {
        "iri": n["iri"],
        "label": n["label"],
        "definition": n["definition"],
        "synonyms": list(n["synonyms"]),
        "edges": [],  # mock has no edges; real graph returns SKOS broader/narrower
    }


def existing_mapping(vendor: str, vendor_product_ref: str) -> Optional[dict]:
    return _PRIOR_MAPPINGS.get((vendor.lower(), vendor_product_ref))


def conflicts(vendor_product_ref: str) -> list[dict]:
    return _CONFLICTS.get(vendor_product_ref, [])


__all__ = ["node_by_iri", "existing_mapping", "conflicts"]
