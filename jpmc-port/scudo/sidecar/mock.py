"""Lexical CDAO candidate mock — Lambda fallback / local retrieval."""

from __future__ import annotations

_CDAO_NODES = [
    {
        "iri": "jpmorgan:data:cdao:EquityResearch",
        "label": "EquityResearch",
        "synonyms": ["equity research", "sell-side research", "estimates"],
    },
    {
        "iri": "jpmorgan:data:cdao:InvestmentData",
        "label": "InvestmentData",
        "synonyms": ["investment data"],
    },
    {
        "iri": "jpmorgan:data:cdao:Fundamentals",
        "label": "Fundamentals",
        "synonyms": ["fundamentals", "financials"],
    },
    {
        "iri": "jpmorgan:data:cdao:Pricing",
        "label": "Pricing",
        "synonyms": ["pricing", "prices", "market data"],
    },
    {
        "iri": "jpmorgan:data:cdao:CorporateActions",
        "label": "CorporateActions",
        "synonyms": ["corporate actions", "dividends"],
    },
    {
        "iri": "jpmorgan:data:cdao:ESG",
        "label": "ESG",
        "synonyms": ["esg", "sustainability"],
    },
    {
        "iri": "jpmorgan:data:cdao:ReferenceData",
        "label": "ReferenceData",
        "synonyms": ["reference data", "symbology"],
    },
    {
        "iri": "jpmorgan:data:cdao:FixedIncome",
        "label": "FixedIncome",
        "synonyms": ["fixed income", "bonds"],
    },
    {
        "iri": "jpmorgan:data:cdao:NewsAndResearch",
        "label": "NewsAndResearch",
        "synonyms": ["news", "research", "transcripts"],
    },
]


def _score(term: str, node: dict) -> float:
    t = term.lower()
    score = 0.0
    if t in node["label"].lower():
        score = max(score, 0.95)
    for syn in node["synonyms"]:
        if t in syn.lower():
            score = max(score, 0.85)
        for word in t.split():
            if word and word in syn.lower():
                score = max(score, 0.6)
    return score


def candidate_nodes(term: str, limit: int = 25) -> list[dict]:
    scored = [(n, _score(term, n)) for n in _CDAO_NODES]
    scored = [(n, s) for n, s in scored if s > 0]
    scored.sort(key=lambda ns: ns[1], reverse=True)
    return [
        {"iri": n["iri"], "label": n["label"], "score": round(s, 3)}
        for n, s in scored[:limit]
    ]


def taxonomy_snapshot() -> list[dict]:
    """Return the bounded local sidecar taxonomy snapshot, read-only."""
    return [
        {
            "iri": node["iri"],
            "label": node["label"],
            "parent_iri": None,
            "children_iris": [],
            "node_kind": "concept",
            "superclass_iris": [],
            "superproperty_iris": [],
        }
        for node in _CDAO_NODES
    ]
