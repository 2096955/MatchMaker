"""
Configuration — single source of truth for the store swap point.

The whole point of the seam is that NOTHING above the store layer knows which
backend is live. That decision is made here, from the environment, and nowhere
else. Set STORE_BACKEND=falkordb for local/dev, STORE_BACKEND=neptune for the
Atlas cutover. The agent, the matcher and the MCP tools are identical either way.

Three-seam vendor-agnostic contract (#18):
    The repeatability seams from the matching strategy live as three env
    vars, each surfacing as a Settings field below. Code SHOULD consult
    Settings (never os.getenv directly) so the deploy task definitions in
    infra/scudo-dev-deploy.yaml are actually load-bearing:

      SCUDO_VENDOR_ADAPTERS  -> Settings.vendor_adapters
      SCUDO_TAXONOMY_LOADER  -> Settings.taxonomy_loader
      SCUDO_PERSIST_TARGET   -> Settings.persist_target

    These three are the only contract points the matching strategy needs to
    swap a client deployment: which vendor catalogues we normalise, which
    classification ontology we load, and where the canonical write lands.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


# Vendors in scope this year. Anything outside this list is rejected by the
# scope gate (see app/frames.py) — deterministically, never by model judgement.
PRIORITY_VENDORS: tuple[str, ...] = (
    "LSEG",
    "S&P Global",
    "Bloomberg",
    "ICE",
    "FactSet",
)

# IRI namespace convention: mds.<vendor>:<uuid5>
IRI_NAMESPACE = "mds"

# Confidence floor. At or above -> auto-mapped. Below -> escalate to a human.
# This is an invariant, enforced in code, not a model preference.
CONFIDENCE_FLOOR: float = 0.80

# Cost-ladder band half-width around the floor. Cases within ±this distance
# of the floor are the "borderline" band — the only band that consults the
# specialist. Tunable per environment.
BORDERLINE_HALF_WIDTH: float = 0.05


def pass_threshold(
    floor: float = CONFIDENCE_FLOOR, half: float = BORDERLINE_HALF_WIDTH
) -> float:
    """Upper band edge (>= this -> PASS).

    Rounded to 2 dp: a naive ``floor + half`` yields 0.8500000000000001 for the
    canonical 0.80/0.05 config, which silently pushes a score of exactly 0.85
    into BORDERLINE. Banding is the product's headline behaviour, so the edge
    must be exact.
    """
    return round(floor + half, 2)


def borderline_threshold(
    floor: float = CONFIDENCE_FLOOR, half: float = BORDERLINE_HALF_WIDTH
) -> float:
    """Lower band edge (>= this -> at least BORDERLINE). Rounded to 2 dp."""
    return round(floor - half, 2)


_ALLOWED_TAXONOMY_LOADERS: tuple[str, ...] = ("cdao",)
_ALLOWED_PERSIST_TARGETS: tuple[str, ...] = ("falkordb", "neptune", "none", "memory")
_ALLOWED_ENRICHMENT_BACKENDS: tuple[str, ...] = ("opus", "off")


def _default_vendor_adapters() -> tuple[str, ...]:
    """Default vendor adapter list — derived from PRIORITY_VENDORS so the
    in-scope vendor list and the wired adapters never drift apart.

    Normalisation matches what the deploy task def emits in
    SCUDO_VENDOR_ADAPTERS: lower-case, spaces collapsed to underscores,
    every other non-alphanumeric character stripped. So:

      "LSEG"        -> "lseg"
      "S&P Global"  -> "sp_global"   (& stripped, space -> _)
      "Bloomberg"   -> "bloomberg"
      "ICE"         -> "ice"
      "FactSet"     -> "factset"

    The strip-non-alphanumeric is load-bearing: without it, "S&P Global"
    would be "s&p_global" — and silently disagree with the `sp_global`
    literal in infra/scudo-dev-deploy.yaml. That is the exact dev-vs-ECS
    drift this seam was opened to eliminate; the smoke gate pins the
    resulting tuple by literal, NOT by re-running the same broken rule.
    """
    import re

    out: list[str] = []
    for v in PRIORITY_VENDORS:
        s = v.lower().replace(" ", "_")
        s = re.sub(r"[^a-z0-9_]", "", s)
        out.append(s)
    return tuple(out)


@dataclass(frozen=True)
class Settings:
    """Frozen settings dataclass — single source of truth for runtime config.

    Existing fields (store swap point):
        store_backend:         "falkordb" | "neptune"
        falkordb_url:          e.g. falkordb://localhost:6379
        neptune_endpoint:      e.g. https://<cluster>.neptune.amazonaws.com:8182
        graph_name:            logical graph / dataset name
        frame_source:          "mock" | "s3"
        s3_bucket:             vendor working-set bucket (prod / M8)
        s3_prefix:             optional sub-prefix (e.g. "env/uat/")
        confidence_floor:      below this similarity → escalate
        borderline_half_width: cost-ladder band around the floor

    Three-seam vendor-agnostic contract (#18):
        vendor_adapters: tuple[str, ...]
            Which vendor catalogue normalisers are wired. Read from
            SCUDO_VENDOR_ADAPTERS (comma-separated, e.g.
            "lseg,bloomberg,sp_global,ice,factset"). Defaults to
            PRIORITY_VENDORS lowercased + "_" for spaces, so the default
            tracks the in-scope vendor list automatically.
        taxonomy_loader: str
            Which classification ontology is loaded. Read from
            SCUDO_TAXONOMY_LOADER. Defaults to "cdao". Allowed values
            today: "cdao" only (will expand for other clients later).
        persist_target: str
            Where the canonical write goes. Read from SCUDO_PERSIST_TARGET.
            Defaults to "falkordb" (matches store_backend in dev).
            Allowed values: "falkordb" | "neptune" | "none".
    """

    store_backend: str  # "falkordb" | "neptune"
    falkordb_url: (
        str  # e.g. falkordb://localhost:6379  (or falkordb:// for default local)
    )
    neptune_endpoint: str  # e.g. https://<cluster>.neptune.amazonaws.com:8182
    graph_name: str  # logical graph / dataset name
    frame_source: str  # "mock" | "s3"
    s3_bucket: str  # vendor working-set bucket (prod / M8)
    s3_prefix: str  # optional sub-prefix (e.g. "env/uat/") — joined as f"{s3_prefix}{vendor}/{product_id}.json"
    confidence_floor: float
    borderline_half_width: float  # cost-ladder band width around the floor
    # Three-seam vendor-agnostic contract — see module docstring (#18).
    vendor_adapters: tuple[str, ...]  # SCUDO_VENDOR_ADAPTERS
    taxonomy_loader: str  # SCUDO_TAXONOMY_LOADER — "cdao" (only allowed today)
    persist_target: str  # SCUDO_PERSIST_TARGET — "falkordb" | "neptune" | "none"
    # Opus-dense feature flag — see method docstring on from_env.
    # When True, find_similar_products delegates to
    # retrieval.multi_path_retrieve (Opus-judged dense scoring with the
    # multi-path shape). When False (default), the existing Jaro-Winkler +
    # BM25 + RRF path runs unchanged. Gated so the 86 smoke gates keep
    # exercising the legacy path until the new path is fully calibrated.
    use_opus_dense: bool  # SCUDO_USE_OPUS_DENSE — "1"/"true"/"yes" -> True
    # M10 conceptual-enrichment backend — see enrichment.py. "off" (default)
    # means extract_field_structure/classify_business_concept abstain with no
    # AWS call, so smoke gates stay green without Bedrock creds. "opus" turns
    # on the real Bedrock call.
    enrichment_backend: str  # SCUDO_ENRICHMENT_BACKEND — "opus" | "off"

    @staticmethod
    def from_env() -> "Settings":
        # s3_prefix: normalise so the lookup is "{prefix}{vendor}/{product_id}.json"
        # without surprises. Empty prefix is the default and means top-of-bucket.
        prefix = os.getenv("S3_PREFIX", "").strip()
        if prefix and not prefix.endswith("/"):
            prefix = prefix + "/"

        # Store backend (existing seam) — needed below to default persist_target.
        store_backend = os.getenv("STORE_BACKEND", "falkordb").lower()

        # --- Three-seam contract (#18) ------------------------------------
        # vendor_adapters: comma-separated; empty entries trimmed; lowercased.
        raw_adapters = os.getenv("SCUDO_VENDOR_ADAPTERS", "").strip()
        if raw_adapters:
            vendor_adapters = tuple(
                tok.strip().lower() for tok in raw_adapters.split(",") if tok.strip()
            )
        else:
            vendor_adapters = _default_vendor_adapters()

        # taxonomy_loader: case-insensitive; validated against allow-list.
        taxonomy_loader = os.getenv("SCUDO_TAXONOMY_LOADER", "cdao").strip().lower()
        if taxonomy_loader not in _ALLOWED_TAXONOMY_LOADERS:
            raise ValueError(
                f"SCUDO_TAXONOMY_LOADER={taxonomy_loader!r} not in "
                f"{_ALLOWED_TAXONOMY_LOADERS!r}"
            )

        # persist_target: defaults to store_backend so dev stays coherent.
        persist_target = (
            os.getenv(
                "SCUDO_PERSIST_TARGET",
                store_backend,
            )
            .strip()
            .lower()
        )
        if persist_target not in _ALLOWED_PERSIST_TARGETS:
            raise ValueError(
                f"SCUDO_PERSIST_TARGET={persist_target!r} not in "
                f"{_ALLOWED_PERSIST_TARGETS!r}"
            )

        # Opus-dense feature flag — accept the common truthy spellings so
        # an operator can flip the path with SCUDO_USE_OPUS_DENSE=1 (or
        # =true / =yes) without remembering the exact token. Everything
        # else (unset, "0", "false", garbage) stays at False so the legacy
        # Jaro-Winkler + BM25 + RRF path remains the default.
        use_opus_dense = os.getenv("SCUDO_USE_OPUS_DENSE", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

        # M10 enrichment backend: case-insensitive; validated against
        # allow-list; defaults to "off" so smoke gates stay green without
        # Bedrock creds until an environment explicitly opts in.
        enrichment_backend = (
            os.getenv("SCUDO_ENRICHMENT_BACKEND", "off").strip().lower()
        )
        if enrichment_backend not in _ALLOWED_ENRICHMENT_BACKENDS:
            raise ValueError(
                f"SCUDO_ENRICHMENT_BACKEND={enrichment_backend!r} not in "
                f"{_ALLOWED_ENRICHMENT_BACKENDS!r}"
            )

        return Settings(
            store_backend=store_backend,
            falkordb_url=os.getenv("FALKORDB_URL", "falkordb://localhost:6379"),
            neptune_endpoint=os.getenv("NEPTUNE_ENDPOINT", ""),
            graph_name=os.getenv("GRAPH_NAME", "scudo_mapping"),
            frame_source=os.getenv("FRAME_SOURCE", "mock").lower(),
            s3_bucket=os.getenv("S3_WORKING_SET_BUCKET", ""),
            s3_prefix=prefix,
            confidence_floor=float(
                os.getenv("CONFIDENCE_FLOOR", str(CONFIDENCE_FLOOR))
            ),
            borderline_half_width=float(
                os.getenv("BORDERLINE_HALF_WIDTH", str(BORDERLINE_HALF_WIDTH)),
            ),
            vendor_adapters=vendor_adapters,
            taxonomy_loader=taxonomy_loader,
            persist_target=persist_target,
            use_opus_dense=use_opus_dense,
            enrichment_backend=enrichment_backend,
        )


settings = Settings.from_env()
