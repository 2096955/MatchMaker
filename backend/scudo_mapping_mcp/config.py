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
                               ("cdao" | "dcat"; see ALLOWED_TAXONOMY_LOADERS)
      SCUDO_TAXONOMY_SOURCE   -> Settings.taxonomy_source (RDF path when dcat)
      SCUDO_PERSIST_TARGET   -> Settings.persist_target

    These three are the only contract points the matching strategy needs to
    swap a client deployment: which vendor catalogues we normalise, which
    classification ontology we load, and where the canonical write lands.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


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

# Confidence band centre. pass_threshold() is the auto-map edge; scores between
# borderline_threshold() and pass_threshold() consult the specialist/reviewer path.
CONFIDENCE_FLOOR: float = 0.75

# Cost-ladder band half-width around the floor. Cases within ±this distance
# of the floor are the "borderline" band — the only band that consults the
# specialist. Tunable per environment.
BORDERLINE_HALF_WIDTH: float = 0.05
PASS_CUT: float = 0.80
FAIL_CUT: float = 0.70


def pass_threshold(
    floor: float = CONFIDENCE_FLOOR, half: float = BORDERLINE_HALF_WIDTH
) -> float:
    """Upper band edge (>= this -> PASS).

    The canonical 0.75/0.05 config is NOT at risk and never reaches the
    rounding: ``0.75 + 0.05`` really is exactly the float 0.8, and the branch
    below short-circuits to ``PASS_CUT`` whenever ``floor``/``half`` are the
    module defaults. ``round(floor + half, 2)`` runs only for OVERRIDDEN
    windows — and those are the ones that break. ``0.80 + 0.05`` yields
    0.8500000000000001, so a naive sum would silently push a score of exactly
    0.85 into BORDERLINE.

    ``floor``/``half`` are overridable per call (both are keyword arguments
    here and are threaded through ``matching.map_vendor_product``) and via the
    ``CONFIDENCE_FLOOR`` / ``BORDERLINE_HALF_WIDTH`` env vars read in
    ``Settings.from_env`` below. Banding is the product's headline behaviour,
    so the edge must be exact for every window, not just the default one.
    """
    if floor == CONFIDENCE_FLOOR and half == BORDERLINE_HALF_WIDTH:
        return PASS_CUT
    return round(floor + half, 2)


def borderline_threshold(
    floor: float = CONFIDENCE_FLOOR, half: float = BORDERLINE_HALF_WIDTH
) -> float:
    """Lower band edge (>= this -> at least BORDERLINE).

    Same shape as ``pass_threshold`` above: the canonical 0.75/0.05 config
    short-circuits to ``FAIL_CUT`` and would be exact anyway (``0.75 - 0.05``
    really is the float 0.7). ``round(floor - half, 2)`` runs only for
    OVERRIDDEN windows, where the subtraction is not exact —
    ``0.85 - 0.05`` yields 0.7999999999999999. Note the error runs the
    OPPOSITE way from ``pass_threshold``'s: the edge lands one ULP BELOW
    0.80, so an exact 0.80 still clears it (``0.80 >= 0.7999999999999999``
    is True). What slips through is the largest float under 0.80 —
    ``math.nextafter(0.80, 0)`` IS that edge — which without the rounding
    would be admitted to BORDERLINE instead of FAIL. Upper edge too strict,
    lower edge too lenient; the rounding fixes both.
    """
    if floor == CONFIDENCE_FLOOR and half == BORDERLINE_HALF_WIDTH:
        return FAIL_CUT
    return round(floor - half, 2)


_ALLOWED_TAXONOMY_LOADERS: tuple[str, ...] = ("cdao", "dcat")
# Public alias — taxonomy_loader registry must stay in sync with this tuple.
ALLOWED_TAXONOMY_LOADERS = _ALLOWED_TAXONOMY_LOADERS
# JPMC-LOCAL: 'local_file' added — durable laptop memory (see store/local_file_store.py).
_ALLOWED_PERSIST_TARGETS: tuple[str, ...] = (
    "falkordb",
    "neptune",
    "none",
    "memory",
    "local_file",
    "scipy_sqlite",
)
_ALLOWED_ENRICHMENT_BACKENDS: tuple[str, ...] = ("opus", "off")
_ALLOWED_DENSE_BACKENDS: tuple[str, ...] = ("opus", "jaro_winkler")


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
            SCUDO_TAXONOMY_LOADER. Defaults to "cdao". Allowed values:
            "cdao" | "dcat".
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
    taxonomy_loader: str  # SCUDO_TAXONOMY_LOADER — "cdao" | "dcat"
    persist_target: str  # SCUDO_PERSIST_TARGET — "falkordb" | "neptune" | "none"
    scipy_sqlite_path: str  # SCUDO_SCIPY_SQLITE_PATH — single-host matching DB
    dense_backend: str  # SCUDO_DENSE_BACKEND — "opus" | "jaro_winkler"
    taxonomy_text_enabled: bool  # SCUDO_TAXONOMY_TEXT — inject SKOS text into matching
    taxonomy_text_shadow: bool  # SCUDO_TAXONOMY_TEXT_SHADOW — log text-on BM25 diff
    # Phase E sub-flag: compose the UML matching signals (businessConcept /
    # assetClass / superAssetClass) into the BM25 doc. Rides the ontology-text
    # channel — it has no effect unless SCUDO_TAXONOMY_TEXT is also on — so
    # its marginal impact is separately measurable and reversible. BM25-ONLY:
    # dense text never sees these fields (floor neutrality vs 0.80/0.70).
    taxonomy_uml_text: bool  # SCUDO_TAXONOMY_UML_TEXT — UML signals into BM25 doc
    # Phase E step 4 flag: plumb the node's loaded asset_class into
    # run_validations' node_data_class so vendor-vs-node asset-class
    # disagreement is a deterministic required-fail. Default OFF (measured
    # rollout: loading a UML-enriched catalogue must not change matching
    # outcomes until this lever is flipped). Deliberately SEPARATE from
    # SCUDO_TAXONOMY_UML_TEXT — the text channel and the validation seam
    # are independent rollout levers.
    asset_class_validation: bool  # SCUDO_ASSET_CLASS_VALIDATION — step 4 seam
    subsumption_depth: int  # SCUDO_SUBSUMPTION_DEPTH — load-time RDFS closure (0=off)
    subsumption_expand: (
        bool  # SCUDO_SUBSUMPTION_EXPAND — 1-hop BM25 candidate expansion
    )
    taxonomy_source: str  # SCUDO_TAXONOMY_SOURCE — RDF path when loader=dcat
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
        # JPMC-LOCAL: the default is 'falkordb', which needs a FalkorDB container.
        # We did NOT change it, because all 7 AWS deploy configs (Dockerfile:51,
        # infra/*.yaml, template.yaml) set STORE_BACKEND=falkordb explicitly and
        # rely on the branch in store/factory.py. Instead, set the env var for a
        # local run -- start_local.py does this for you:
        #     STORE_BACKEND=local_file   (durable laptop memory; recommended)
        #     STORE_BACKEND=memory       (same, but forgets on restart)
        # Neither needs FalkorDB or Neptune installed.
        # JPMC-LOCAL: the default is 'local_file', NOT 'falkordb'.
        #
        # It used to be 'falkordb', which meant any entry point that forgot to
        # set STORE_BACKEND tried to open a FalkorDB connection — start_all.sh
        # sets nothing, so "FalkorDB keeps being asked for" was the result of
        # running the obvious script. The failure was also confusing: a
        # connection error to :6379 rather than "you have not configured a
        # store".
        #
        # Changing the DEFAULT is safe because every deployed path sets the var
        # EXPLICITLY — verified: backend/Dockerfile:51,
        # infra/scudo-dev-deploy.yaml (4 containers), infra/scudo-poc-app.yaml,
        # backend/scudo/template.yaml. So this only changes runs that
        # configured nothing, which is precisely the local case that should
        # never have needed a container.
        #
        # 'local_file' is the full matching ladder over a file-backed store:
        # real JW+BM25+RRF scoring, and HITL decisions survive a restart.
        store_backend = os.getenv("STORE_BACKEND", "local_file").lower()

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
        if not persist_target:
            persist_target = store_backend
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

        dense_backend = os.getenv("SCUDO_DENSE_BACKEND", "jaro_winkler").strip().lower()
        if dense_backend not in _ALLOWED_DENSE_BACKENDS:
            raise ValueError(
                f"SCUDO_DENSE_BACKEND={dense_backend!r} not in "
                f"{_ALLOWED_DENSE_BACKENDS!r}"
            )

        taxonomy_text_enabled = (
            os.getenv("SCUDO_TAXONOMY_TEXT", "").strip().lower() in _TRUTHY_ENV
        )

        taxonomy_text_shadow = (
            os.getenv("SCUDO_TAXONOMY_TEXT_SHADOW", "").strip().lower() in _TRUTHY_ENV
        )

        taxonomy_uml_text = (
            os.getenv("SCUDO_TAXONOMY_UML_TEXT", "").strip().lower() in _TRUTHY_ENV
        )

        asset_class_validation = (
            os.getenv("SCUDO_ASSET_CLASS_VALIDATION", "").strip().lower() in _TRUTHY_ENV
        )

        subsumption_depth = int(os.getenv("SCUDO_SUBSUMPTION_DEPTH", "0"))
        subsumption_expand = (
            os.getenv("SCUDO_SUBSUMPTION_EXPAND", "").strip().lower() in _TRUTHY_ENV
        )

        taxonomy_source = os.getenv("SCUDO_TAXONOMY_SOURCE", "").strip()
        default_scipy_sqlite_path = str(
            Path(__file__).resolve().parents[1] / ".local" / "scudo_matching.sqlite3"
        )
        scipy_sqlite_path = (
            os.getenv("SCUDO_SCIPY_SQLITE_PATH", "").strip()
            or default_scipy_sqlite_path
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
            scipy_sqlite_path=scipy_sqlite_path,
            dense_backend=dense_backend,
            taxonomy_text_enabled=taxonomy_text_enabled,
            taxonomy_text_shadow=taxonomy_text_shadow,
            taxonomy_uml_text=taxonomy_uml_text,
            asset_class_validation=asset_class_validation,
            subsumption_depth=subsumption_depth,
            subsumption_expand=subsumption_expand,
            taxonomy_source=taxonomy_source,
            use_opus_dense=use_opus_dense,
            enrichment_backend=enrichment_backend,
        )


_TRUTHY_ENV = frozenset({"1", "true", "yes", "on"})


def env_dense_backend() -> str:
    """Live ``SCUDO_DENSE_BACKEND`` — re-reads env for smoke/tests."""
    backend = os.getenv("SCUDO_DENSE_BACKEND", "jaro_winkler").strip().lower()
    if backend not in _ALLOWED_DENSE_BACKENDS:
        raise ValueError(
            f"SCUDO_DENSE_BACKEND={backend!r} not in {_ALLOWED_DENSE_BACKENDS!r}"
        )
    return backend


def env_use_opus_dense() -> bool:
    """Live ``SCUDO_USE_OPUS_DENSE`` — re-reads env for smoke/tests."""
    return os.getenv("SCUDO_USE_OPUS_DENSE", "").strip().lower() in _TRUTHY_ENV


def env_taxonomy_text_enabled() -> bool:
    """Live ``SCUDO_TAXONOMY_TEXT`` — re-reads env for smoke/tests."""
    return os.getenv("SCUDO_TAXONOMY_TEXT", "").strip().lower() in _TRUTHY_ENV


def env_taxonomy_text_shadow_enabled() -> bool:
    """Live ``SCUDO_TAXONOMY_TEXT_SHADOW`` — re-reads env for smoke/tests."""
    return os.getenv("SCUDO_TAXONOMY_TEXT_SHADOW", "").strip().lower() in _TRUTHY_ENV


def env_taxonomy_uml_text_enabled() -> bool:
    """Live ``SCUDO_TAXONOMY_UML_TEXT`` — re-reads env for smoke/tests."""
    return os.getenv("SCUDO_TAXONOMY_UML_TEXT", "").strip().lower() in _TRUTHY_ENV


def env_asset_class_validation_enabled() -> bool:
    """Live ``SCUDO_ASSET_CLASS_VALIDATION`` — re-reads env for smoke/tests."""
    return os.getenv("SCUDO_ASSET_CLASS_VALIDATION", "").strip().lower() in _TRUTHY_ENV


def env_subsumption_expand() -> bool:
    """Live ``SCUDO_SUBSUMPTION_EXPAND`` — re-reads env for smoke/tests."""
    return os.getenv("SCUDO_SUBSUMPTION_EXPAND", "").strip().lower() in _TRUTHY_ENV


def env_input_completeness_validation_enabled() -> bool:
    """Live ``SCUDO_INPUT_COMPLETENESS_VALIDATION`` — re-reads env.

    Gates the input-thinness validation (validations.py). Default off so
    existing artifacts stay byte-identical; env-only by design (no Settings
    snapshot field — call-time read like the other measured-rollout flags).
    """
    return (
        os.getenv("SCUDO_INPUT_COMPLETENESS_VALIDATION", "").strip().lower()
        in _TRUTHY_ENV
    )


def env_temporal_validation_enabled() -> bool:
    """Live ``SCUDO_TEMPORAL_VALIDATION`` — re-reads env.

    Gates the temporal-compatibility validation (validations.py). Default off
    so existing artifacts stay byte-identical; env-only by design (no Settings
    snapshot field — call-time read like the other measured-rollout flags).

    Flag ON, the check is still pass-by-default whenever either side declares
    no parseable coverage — only a positive disjoint-interval disagreement is
    a required failure. A missing date never fails a match.
    """
    return os.getenv("SCUDO_TEMPORAL_VALIDATION", "").strip().lower() in _TRUTHY_ENV


def env_margin_gate_enabled() -> bool:
    """Live ``SCUDO_MARGIN_GATE`` — re-reads env.

    Gates the top1-vs-top2 margin demotion at Rung 5 (matching.py). Default
    off; env-only by design (no Settings snapshot field).
    """
    return os.getenv("SCUDO_MARGIN_GATE", "").strip().lower() in _TRUTHY_ENV


# Minimum top1−top2 similarity lead required for an unassisted auto-map when
# SCUDO_MARGIN_GATE is on. Below this, the case is a near-tie the string
# scorer cannot adjudicate — route to review.
MARGIN_MIN_DEFAULT: float = 0.02


def env_margin_min() -> float:
    """Live ``SCUDO_MARGIN_MIN`` — re-reads env; clamped to [0.0, 1.0).

    A malformed value falls back to ``MARGIN_MIN_DEFAULT`` rather than
    raising — the margin gate is a tightening lever, and a config typo must
    not take the matcher down.
    """
    raw = os.getenv("SCUDO_MARGIN_MIN", "").strip()
    if not raw:
        return MARGIN_MIN_DEFAULT
    try:
        v = float(raw)
    except ValueError:
        return MARGIN_MIN_DEFAULT
    if v != v or v < 0.0 or v >= 1.0:  # NaN / out of band
        return MARGIN_MIN_DEFAULT
    return v


settings = Settings.from_env()
