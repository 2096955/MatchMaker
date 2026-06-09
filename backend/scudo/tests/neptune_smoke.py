"""Real-AWS-mode smoke — runs the two backends end-to-end against AWS.

Two distinct stores after the two-graphs split:
  authoritative   → NEPTUNE_ENDPOINT (RDF graph; SPARQL via Neptune MCP Server)
  sidecar         → SCUDO_SIDECAR_GRAPH_ENDPOINT (lexical retrieval)

Either env var enables that backend's smoke section. With neither set, the
driver SKIPs cleanly and prints the env vars an operator needs to flip on.

Run (full):
  NEPTUNE_ENDPOINT=auth-cluster.cluster-xxx.us-east-1.neptune.amazonaws.com \\
  SCUDO_SIDECAR_GRAPH_ENDPOINT=sidecar.cluster-yyy.us-east-1.neptune.amazonaws.com \\
  AWS_REGION=us-east-1 \\
  python -m scudo.tests.neptune_smoke
"""
from __future__ import annotations

import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from scudo import authoritative as auth_facade
from scudo import sidecar as side_facade
from scudo.authoritative import client as auth_client
from scudo.sidecar import client as side_client
from scudo.shared import bedrock_embed_id, bedrock_llm_id


def _env_section(title: str, required: list[str], recommended: list[str]) -> bool:
    missing = [k for k in required if not os.environ.get(k)]
    print(f"\n── {title} ──")
    for k in required:
        v = os.environ.get(k)
        print(f"  {k:<35} {'set' if v else 'REQUIRED'}")
    for k in recommended:
        v = os.environ.get(k)
        print(f"  {k:<35} {'set' if v else 'recommended (default applies)'}")
    if missing:
        print(f"  → SKIP {title.lower()} (missing: {missing})")
        return False
    return True


def main() -> int:
    print(f"LLM model:        {bedrock_llm_id()}")
    print(f"Embed model:      {bedrock_embed_id()}")

    auth_ok = _env_section(
        "Authoritative graph",
        required=["NEPTUNE_ENDPOINT", "AWS_REGION"],
        recommended=["NEPTUNE_USE_IAM_AUTH", "NEPTUNE_MCP_CMD", "NEPTUNE_MCP_QUERY_TOOL"],
    )
    side_ok = _env_section(
        "Lexical sidecar",
        required=["SCUDO_SIDECAR_GRAPH_ENDPOINT", "AWS_REGION"],
        recommended=["SCUDO_SIDECAR_VECTOR_STORE_URI", "SCUDO_SIDECAR_USE_IAM",
                     "BEDROCK_LLM_MODEL_ID", "BEDROCK_EMBEDDING_MODEL_ID"],
    )

    if not (auth_ok or side_ok):
        print("\nNeither backend is configured — nothing to exercise.")
        return 0

    if auth_ok:
        print(f"\n→ scudo.authoritative on {auth_client.neptune_endpoint()}")
        print("  existing_mapping(vendor='lseg', vendor_product_ref='LSEG-IBES-EST-001'):")
        em = auth_facade.existing_mapping(vendor="lseg", vendor_product_ref="LSEG-IBES-EST-001")
        print(f"    {em!r}")
        print("  conflicts(vendor_product_ref='LSEG-IBES-EST-001'):")
        for c in auth_facade.conflicts(vendor_product_ref="LSEG-IBES-EST-001"):
            print(f"    - {c}")
        print("  node_by_iri('jpmorgan:data:cdao:EquityResearch'):")
        n = auth_facade.node_by_iri("jpmorgan:data:cdao:EquityResearch")
        print(f"    {n!r}")

    if side_ok:
        print(f"\n→ scudo.sidecar on {side_client.sidecar_graph_endpoint()}")
        print("  candidate_nodes('equity research', limit=5):")
        for c in side_facade.candidate_nodes(term="equity research", limit=5):
            print(f"    - {c['iri']:55s} score={c['score']:.3f}  {c['label']}")

    print("\nREAL-MODE SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
