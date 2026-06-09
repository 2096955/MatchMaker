"""
Configuration — single source of truth for the store swap point.

The whole point of the seam is that NOTHING above the store layer knows which
backend is live. That decision is made here, from the environment, and nowhere
else. Set STORE_BACKEND=falkordb for local/dev, STORE_BACKEND=neptune for the
Atlas cutover. The agent, the matcher and the MCP tools are identical either way.
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


@dataclass(frozen=True)
class Settings:
    store_backend: str            # "falkordb" | "neptune"
    falkordb_url: str             # e.g. falkordb://localhost:6379  (or falkordb:// for default local)
    neptune_endpoint: str         # e.g. https://<cluster>.neptune.amazonaws.com:8182
    graph_name: str               # logical graph / dataset name
    frame_source: str             # "mock" | "s3"
    s3_bucket: str                # vendor working-set bucket (prod / M8)
    s3_prefix: str                # optional sub-prefix (e.g. "env/uat/") — joined as f"{s3_prefix}{vendor}/{product_id}.json"
    confidence_floor: float
    borderline_half_width: float  # cost-ladder band width around the floor

    @staticmethod
    def from_env() -> "Settings":
        # s3_prefix: normalise so the lookup is "{prefix}{vendor}/{product_id}.json"
        # without surprises. Empty prefix is the default and means top-of-bucket.
        prefix = os.getenv("S3_PREFIX", "").strip()
        if prefix and not prefix.endswith("/"):
            prefix = prefix + "/"
        return Settings(
            store_backend=os.getenv("STORE_BACKEND", "falkordb").lower(),
            falkordb_url=os.getenv("FALKORDB_URL", "falkordb://localhost:6379"),
            neptune_endpoint=os.getenv("NEPTUNE_ENDPOINT", ""),
            graph_name=os.getenv("GRAPH_NAME", "scudo_mapping"),
            frame_source=os.getenv("FRAME_SOURCE", "mock").lower(),
            s3_bucket=os.getenv("S3_WORKING_SET_BUCKET", ""),
            s3_prefix=prefix,
            confidence_floor=float(os.getenv("CONFIDENCE_FLOOR", str(CONFIDENCE_FLOOR))),
            borderline_half_width=float(
                os.getenv("BORDERLINE_HALF_WIDTH", str(BORDERLINE_HALF_WIDTH)),
            ),
        )


settings = Settings.from_env()
