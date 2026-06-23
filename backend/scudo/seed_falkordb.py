"""Seed the CDAO taxonomy into FalkorDB so the real matcher has nodes to retrieve.

The deployed us-east-1 Lambda runs the REAL cost-ladder matcher
(``scudo_mapping_mcp.matching.map_vendor_product``) over a FalkorDB OSS
instance instead of mock candidates. That matcher only works once the
TaxonomyNode graph it ranks against actually exists — an empty graph yields
zero candidates and every product lands in NEEDS_REVIEW. This script puts the
nodes there.

It is a STANDALONE, RE-RUNNABLE seeder:

  * Connects through the SAME store factory the matcher uses
    (``scudo_mapping_mcp.store.get_store`` → ``FalkorDBStore``), so the seed
    lands in exactly the graph the matcher reads (STORE_BACKEND=falkordb,
    FALKORDB_URL + GRAPH_NAME from the environment). No second connection
    path to drift.
  * Idempotent — ``upsert_taxonomy_node`` is a Cypher MERGE, so re-running the
    seed never duplicates nodes; it converges the graph to the fixture.
  * Source of nodes is EITHER a local JSON fixture (argv / SCUDO_TAXONOMY_SEED)
    OR a small hard-coded set of representative CDAO nodes so the script runs
    standalone for the PoC with no extra files.

Run (against an up FalkorDB):

    STORE_BACKEND=falkordb \\
    FALKORDB_URL=falkordb://falkordb.scudo.local:6379 \\
    python -m scudo.seed_falkordb

    # or from a fixture
    python -m scudo.seed_falkordb path/to/cdao_taxonomy.json

The fixture is a JSON list of objects, each with ``iri`` and ``label`` and
(optionally) ``parent_iri`` / ``description``. ``description`` is accepted for
forward-compatibility but is NOT persisted by the current store seam (the
TaxonomyNode contract carries iri / label / parent_iri / children_iris only);
it is retained in the fixture as human-readable documentation.

Imports of the heavy dependencies (the ``scudo_mapping_mcp`` config + store,
which pull in the ``falkordb`` client) are LAZY and live inside ``main`` /
helpers, so ``import scudo.seed_falkordb`` and the offline smoke
(``python -m scudo.tests.smoke``) stay import-clean with no creds and no
``falkordb`` package installed.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Optional

# Env var an operator can set instead of passing the fixture path on argv.
_FIXTURE_ENV = "SCUDO_TAXONOMY_SEED"


# A small, representative slice of the JPMorgan CDAO classification taxonomy.
# Enough structure (a root + a few leaf classes under it) that the matcher's
# Jaro-Winkler + BM25 + RRF retrieval has something meaningful to rank for the
# PoC. IRIs follow the jpmorgan:data:cdao:<Class> convention. ``description``
# is documentation only (see module docstring) — the store persists iri / label
# / parent_iri.
_DEFAULT_NODES: tuple[dict[str, Any], ...] = (
    {
        "iri": "jpmorgan:data:cdao:MarketData",
        "label": "Market Data",
        "description": "Root class for market-observed reference and pricing data.",
    },
    {
        "iri": "jpmorgan:data:cdao:EquityPrices",
        "label": "Equity Prices",
        "parent_iri": "jpmorgan:data:cdao:MarketData",
        "description": "End-of-day and intraday equity price observations.",
    },
    {
        "iri": "jpmorgan:data:cdao:FixedIncomePrices",
        "label": "Fixed Income Prices",
        "parent_iri": "jpmorgan:data:cdao:MarketData",
        "description": "Bond and other fixed-income instrument pricing.",
    },
    {
        "iri": "jpmorgan:data:cdao:FXRates",
        "label": "FX Rates",
        "parent_iri": "jpmorgan:data:cdao:MarketData",
        "description": "Foreign-exchange spot and forward rates.",
    },
    {
        "iri": "jpmorgan:data:cdao:ReferenceData",
        "label": "Reference Data",
        "description": "Root class for static instrument and entity reference data.",
    },
    {
        "iri": "jpmorgan:data:cdao:CorporateActions",
        "label": "Corporate Actions",
        "parent_iri": "jpmorgan:data:cdao:ReferenceData",
        "description": "Dividends, splits, mergers and other corporate events.",
    },
)


def _redact_url(url: str) -> str:
    """Return ``url`` with any embedded password redacted for logging.

    The FalkorDB URL convention here is bare ``falkordb://host:port`` with no
    credentials, but a deployment may inject ``falkordb://user:pass@host:port``.
    Never print the secret if one is present.
    """
    if "@" not in url:
        return url
    scheme, _, rest = url.partition("://")
    creds, _, host = rest.rpartition("@")
    if ":" in creds:
        user, _, _ = creds.partition(":")
        creds = f"{user}:***"
    elif creds:
        creds = "***"
    sep = "://" if scheme else ""
    return f"{scheme}{sep}{creds}@{host}" if creds else f"{scheme}{sep}{host}"


def _load_fixture(path: str) -> list[dict[str, Any]]:
    """Load taxonomy node dicts from a JSON fixture file.

    Accepts either a top-level list of node objects, or an object with a
    ``"nodes"`` key holding that list. Raises a clear error on a malformed
    fixture rather than letting a cryptic KeyError surface downstream.
    """
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict) and "nodes" in data:
        data = data["nodes"]
    if not isinstance(data, list):
        raise ValueError(
            f"Taxonomy fixture {path!r} must be a JSON list of node objects "
            f"(or an object with a 'nodes' list), got {type(data).__name__}."
        )
    nodes: list[dict[str, Any]] = []
    for i, raw in enumerate(data):
        if not isinstance(raw, dict):
            raise ValueError(
                f"Taxonomy fixture {path!r} entry {i} is not an object: {raw!r}"
            )
        if not raw.get("iri") or not raw.get("label"):
            raise ValueError(
                f"Taxonomy fixture {path!r} entry {i} is missing required "
                f"'iri' and/or 'label': {raw!r}"
            )
        nodes.append(raw)
    return nodes


def _to_taxonomy_node(raw: dict[str, Any]):
    """Build a ``TaxonomyNode`` from a raw fixture/default dict.

    LAZY import of the model so the module stays import-clean offline. Only the
    fields the store actually persists (iri / label / parent_iri) are passed;
    ``description`` is intentionally dropped (not part of the TaxonomyNode
    contract — see module docstring).
    """
    from scudo_mapping_mcp.models import TaxonomyNode  # lazy

    return TaxonomyNode(
        iri=str(raw["iri"]),
        label=str(raw["label"]),
        parent_iri=(raw.get("parent_iri") or None),
    )


def _resolve_fixture_path(argv: list[str]) -> Optional[str]:
    """First positional arg, else the SCUDO_TAXONOMY_SEED env var, else None."""
    if len(argv) > 1 and argv[1].strip():
        return argv[1].strip()
    env = os.getenv(_FIXTURE_ENV, "").strip()
    return env or None


def seed(nodes: list[dict[str, Any]], store) -> int:
    """Upsert each node into ``store`` idempotently. Returns the count upserted.

    ``store`` is any ``RetrievalStore`` (the FalkorDB store in deployment).
    Kept dependency-light so a caller/test can pass an in-memory store.
    """
    count = 0
    for raw in nodes:
        store.upsert_taxonomy_node(_to_taxonomy_node(raw))
        count += 1
    return count


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entrypoint. Returns a process exit code (0 = success)."""
    argv = list(sys.argv if argv is None else argv)

    # Resolve the node source first — this needs no FalkorDB and gives a clear
    # error before we attempt a connection.
    fixture_path = _resolve_fixture_path(argv)
    if fixture_path:
        try:
            nodes = _load_fixture(fixture_path)
        except FileNotFoundError:
            print(
                f"[seed_falkordb] ERROR: fixture not found: {fixture_path}",
                file=sys.stderr,
            )
            return 2
        except (ValueError, json.JSONDecodeError) as exc:
            print(
                f"[seed_falkordb] ERROR: bad fixture {fixture_path!r}: {exc}",
                file=sys.stderr,
            )
            return 2
        source = f"fixture {fixture_path!r}"
    else:
        nodes = list(_DEFAULT_NODES)
        source = "built-in default CDAO node set"

    # LAZY import of config + store factory so offline import stays clean and
    # the falkordb client is only required at run time.
    from scudo_mapping_mcp.config import settings
    from scudo_mapping_mcp.store import get_store, reset_store_cache

    if settings.store_backend != "falkordb":
        print(
            f"[seed_falkordb] ERROR: STORE_BACKEND={settings.store_backend!r}, "
            f"expected 'falkordb'. Set STORE_BACKEND=falkordb so the seed lands "
            f"in the graph the deployed matcher reads.",
            file=sys.stderr,
        )
        return 2

    redacted_url = _redact_url(settings.falkordb_url)
    print(
        f"[seed_falkordb] backend=falkordb url={redacted_url} "
        f"graph={settings.graph_name!r} source={source} nodes={len(nodes)}"
    )

    # Construct the store via the factory. The factory caches; reset first so a
    # stale handle from an earlier import in the same process is not reused.
    reset_store_cache()
    try:
        store = get_store()
    except ImportError as exc:
        print(
            f"[seed_falkordb] ERROR: the 'falkordb' client is not installed: {exc}. "
            f"Install it (pip install falkordb) to seed a real instance.",
            file=sys.stderr,
        )
        return 3
    except Exception as exc:  # connection/parse errors from the client
        print(
            f"[seed_falkordb] ERROR: could not construct the FalkorDB store at "
            f"{redacted_url}: {exc}",
            file=sys.stderr,
        )
        return 3

    # Fail fast with a clear message if the instance is unreachable, rather than
    # surfacing a cryptic error on the first upsert.
    try:
        healthy = store.health()
    except Exception as exc:
        healthy = False
        print(f"[seed_falkordb] WARN: health check raised: {exc}", file=sys.stderr)
    if not healthy:
        print(
            f"[seed_falkordb] ERROR: FalkorDB at {redacted_url} is unreachable "
            f"(health check failed). Is the instance up and the URL correct?",
            file=sys.stderr,
        )
        store.close()
        return 3

    try:
        upserted = seed(nodes, store)
    except Exception as exc:
        print(f"[seed_falkordb] ERROR: upsert failed: {exc}", file=sys.stderr)
        store.close()
        return 4
    finally:
        # close() swallows its own errors; safe to call even after success.
        pass

    store.close()
    print(
        f"[seed_falkordb] DONE: {upserted} taxonomy node(s) upserted into "
        f"graph {settings.graph_name!r} at {redacted_url}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
