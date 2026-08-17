"""Deterministic agents for local e2e — simulate agentic tool traces.

Swap for Bedrock (``SCUDO_AGENT_MODE=bedrock``) in production. These fakes
implement ``agentic_structured`` so the orchestrator exercises the same
multi-turn path Mapping + Verifier use under Bedrock.
"""

from __future__ import annotations

import json
import re

from .agent_loop import AgentLoopResult
from .catalogue_fill import CatalogueFillResult, DatasetFill, DistributionFill
from .schemas import (
    Band,
    Evidence,
    MappingResult,
    VerifierDimension,
    VerifierReport,
    VerifierScore,
)


def _extract_bundle(prompt: str) -> dict:
    for marker in ("BriefBundle:\n", "BriefBundle (everything"):
        pos = prompt.find(marker)
        if pos >= 0:
            start = prompt.find("{", pos)
            if start < 0:
                return {}
            obj, _ = json.JSONDecoder().raw_decode(prompt[start:])
            return obj
    start = prompt.rfind("\n{")
    if start >= 0:
        obj, _ = json.JSONDecoder().raw_decode(prompt[start + 1 :])
        return obj
    return {}


def _pick_target(prompt: str, candidates: list[dict]) -> str:
    """Highest retrieval score; lexical label hit in prompt breaks ties."""
    if not candidates:
        return "jpmorgan:data:cdao:EquityResearch"
    pl = (prompt or "").lower()
    scored: list[tuple[float, str]] = []
    for c in candidates:
        label = str(c.get("label") or "").lower()
        bonus = 0.2 if label and label in pl else 0.0
        scored.append((float(c.get("score") or 0) + bonus, str(c["iri"])))
    scored.sort(reverse=True)
    return scored[0][1]


class DeterministicMappingAgent:
    """Simulates retrieve → confirm → MappingResult (agentic loop)."""

    last_turns: int = 0

    def agentic_structured(self, output_model, prompt: str, *, max_turns: int = 64):
        del max_turns  # unlimited locally; always settles in a few turns
        result = self(prompt, structured_output_model=output_model).structured_output
        tool_calls = [
            {"name": "describe_system_context"},
            {"name": "graphrag_retrieve"},
            {"name": "neptune_node_by_iri"},
            {"name": "neptune_existing_mapping"},
        ]
        reasoning = [
            "Read vendor_assertion and bundle.candidates.",
            "Retrieved lexical candidates; confirmed top IRI authoritatively.",
            "Checked prior mapping; calibrated confidence to evidence.",
        ]
        self.last_turns = len(tool_calls) + 1
        return AgentLoopResult(
            output=result,
            turns=self.last_turns,
            tool_calls=tool_calls,
            reasoning_trace=reasoning,
        )

    def __call__(self, prompt: str, structured_output_model=None):
        if structured_output_model is None:
            return "ontology-gap write-up for owner review"
        bundle = _extract_bundle(prompt)
        vendor_iri = (
            bundle.get("vendor_product_iri")
            or "mds.lseg:00000000-0000-4000-8000-000000000001"
        )
        candidates = bundle.get("candidates") or []
        # Multi-candidate shortlists: pick highest score with lexical bonus —
        # never assume candidates[0] is the planted answer.
        target = _pick_target(prompt or "", candidates)
        snapshot = bundle.get("ontology_snapshot") or "cdao-2026-05-19"
        m = re.search(r"Ontology snapshot:\s*(\S+)", prompt)
        if m and m.group(1) != "<unset>":
            snapshot = m.group(1)
        result = MappingResult(
            vendor_product_iri=vendor_iri,
            proposed_target_iri=target,
            rationale="Top candidate matches vendor assertion lexical signal.",
            confidence=0.92,
            band=Band.HIGH,
            evidence=[
                Evidence(
                    claim="candidate fit",
                    source_iris=[target, snapshot],
                    quote=snapshot,
                )
            ],
            proposed_triples=[],
        )
        return type("R", (), {"structured_output": result})()

    def structured_output(self, model, prompt: str):
        return self(prompt, structured_output_model=model).structured_output


class DeterministicVerifierAgent:
    """Simulates investigate → score VerifierReport (agentic loop)."""

    last_turns: int = 0

    def agentic_structured(self, output_model, prompt: str, *, max_turns: int = 64):
        del max_turns
        report = self(prompt, structured_output_model=output_model).structured_output
        tool_calls = [
            {"name": "neptune_node_by_iri"},
            {"name": "neptune_existing_mapping"},
            {"name": "lookup_catalogue_term"},
        ]
        reasoning = [
            "Confirmed proposed_target_iri exists in authoritative store.",
            "Checked evidence source_iris and ontology_snapshot citation.",
            "Adversarial pass: confidence calibrated; no rights force-fit.",
        ]
        self.last_turns = len(tool_calls) + 1
        return AgentLoopResult(
            output=report,
            turns=self.last_turns,
            tool_calls=tool_calls,
            reasoning_trace=reasoning,
        )

    def __call__(self, prompt: str, structured_output_model=None):
        report = VerifierReport(
            scores=[VerifierScore(dimension=d, score=2) for d in VerifierDimension],
            total_score=20,
            defects=[],
            rubric_version="rubric-v1",
        )
        return type("R", (), {"structured_output": report})()

    def structured_output(self, model, prompt: str):
        return self(prompt, structured_output_model=model).structured_output


class DeterministicCatalogueFillAgent:
    """Fills CatalogueOntology dataset fields from assertion keys / csvw aliases."""

    def __call__(self, prompt: str, structured_output_model=None):
        assertion: dict = {}
        vendor, ref = "unknown", "unknown"
        for line in prompt.splitlines():
            if line.startswith("vendor: "):
                vendor = line.split(":", 1)[1].strip()
            elif line.startswith("vendor_product_ref: "):
                ref = line.split(":", 1)[1].strip()
        idx = prompt.find("vendor_assertion:")
        if idx >= 0:
            start = prompt.find("{", idx)
            if start >= 0:
                try:
                    assertion, _ = json.JSONDecoder().raw_decode(prompt[start:])
                except json.JSONDecodeError:
                    assertion = {}

        def _get(*keys):
            for k in keys:
                if k in assertion and assertion[k] not in (None, ""):
                    return assertion[k]
            lower = {str(k).lower(): v for k, v in assertion.items()}
            for k in keys:
                if k.lower() in lower and lower[k.lower()] not in (None, ""):
                    return lower[k.lower()]
            return None

        keywords = _get("keyword", "SearchKeywordText", "keywords") or []
        if isinstance(keywords, str):
            keywords = [keywords]
        themes = _get("theme", "SubDomainName") or []
        if isinstance(themes, str):
            themes = [themes]
        result = CatalogueFillResult(
            vendor=vendor,
            vendor_product_ref=ref,
            dataset=DatasetFill(
                dataset_class="cat:DistributedDataset",
                identifier=_get("identifier", "PermID", "Code", "product_id"),
                title=_get("title", "name", "Title"),
                description=_get("description", "SummaryText"),
                features_and_benefits_description=_get(
                    "features_and_benefits_description",
                    "featuresAndBenefitsDescription",
                ),
                keyword=list(keywords),
                theme=list(themes),
                business_concept=_get(
                    "business_concept", "businessConcept", "DomainName"
                ),
                asset_class=_get("asset_class", "assetClass", "AssetClassPermId"),
                super_asset_class=_get(
                    "super_asset_class", "superAssetClass", "ParentAssetClassPermId"
                ),
                geographic_coverage=_get(
                    "geographic_coverage",
                    "CoverageGeographyDescription",
                    "KeyDetailsRegion",
                ),
                temporal_coverage=_get(
                    "temporal_coverage", "CoverageHistoryDescription"
                ),
                industry_coverage=_get(
                    "industry_coverage", "CoverageIndustryDescription"
                ),
                content_type_coverage=_get(
                    "content_type_coverage", "CoverageDataTypeDescription"
                ),
                landing_page=_get("landing_page", "LandingPageUrl", "CatalogueUrl"),
                accrual_periodicity=_get(
                    "accrual_periodicity", "MinimumDatasetFrequencyValue"
                ),
            ),
            distributions=[
                DistributionFill(
                    access_url=_get("access_url", "Uri", "accessURL"),
                    media_type=_get("media_type", "MediaTypeCode"),
                )
            ]
            if _get("access_url", "Uri", "accessURL", "media_type", "MediaTypeCode")
            else [],
            unmapped_source_fields=[],
            requires_human_review=False,
            rationale="Filled Dataset/Distribution from assertion using CatalogueOntology csvw aliases.",
            confidence=0.85,
        )
        return type("R", (), {"structured_output": result})()

    def structured_output(self, model, prompt: str):
        return self(prompt, structured_output_model=model).structured_output
