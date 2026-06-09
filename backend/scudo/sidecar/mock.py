"""In-memory sidecar mock — lexical scoring over a CDAO taxonomy fixture.

The mock mirrors what `retrieval.py` returns in real mode: a list of
{iri, label, score} dicts ranked by relatedness. Used by the smoke tests
and any local dev path without the sidecar stores configured.
"""
from __future__ import annotations


# Tiny CDAO-shaped taxonomy. Shares the shape (but not necessarily identity)
# with scudo.authoritative.mock so the two-graphs distinction stays
# observable even in mock mode.
_CDAO_NODES = [
    {
        "iri": "jpmorgan:data:cdao:EquityResearch",
        "label": "EquityResearch",
        "synonyms": ["equity research", "sell-side research", "stock research", "estimates"],
    },
    {
        "iri": "jpmorgan:data:cdao:InvestmentData",
        "label": "InvestmentData",
        "synonyms": ["investment data", "investment information"],
    },
    {
        "iri": "jpmorgan:data:cdao:Fundamentals",
        "label": "Fundamentals",
        "synonyms": ["fundamentals", "financials", "company financials"],
    },
    {
        "iri": "jpmorgan:data:cdao:Pricing",
        "label": "Pricing",
        "synonyms": ["pricing", "prices", "eod prices", "market data"],
    },
    {
        "iri": "jpmorgan:data:cdao:CorporateActions",
        "label": "CorporateActions",
        "synonyms": ["corporate actions", "events", "dividends", "splits"],
    },
    {
        "iri": "jpmorgan:data:cdao:ESG",
        "label": "ESG",
        "synonyms": ["esg", "sustainability", "carbon"],
    },
    {
        "iri": "jpmorgan:data:cdao:ReferenceData",
        "label": "ReferenceData",
        "synonyms": ["reference data", "static data", "symbology"],
    },
    {
        "iri": "jpmorgan:data:cdao:FixedIncome",
        "label": "FixedIncome",
        "synonyms": ["fixed income", "bonds", "credit"],
    },
    {
        "iri": "jpmorgan:data:cdao:NewsAndResearch",
        "label": "NewsAndResearch",
        "synonyms": ["news", "research", "newswire", "transcripts"],
    },
]


def _score(term: str, node: dict) -> float:
    """Cheap lexical score — substring + word-overlap on label/synonyms.

    Stands in for PropertyGraphIndex + BedrockEmbedding retrieval. Output
    shape matches the real path so the caller is mode-agnostic.
    """
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
    scored = [(node, _score(term, node)) for node in _CDAO_NODES]
    scored = [(n, s) for n, s in scored if s > 0]
    scored.sort(key=lambda ns: ns[1], reverse=True)
    return [
        {"iri": n["iri"], "label": n["label"], "score": round(s, 3)}
        for n, s in scored[:limit]
    ]


__all__ = ["candidate_nodes"]
