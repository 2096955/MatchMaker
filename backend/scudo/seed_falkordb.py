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

After migrating to a new catalogue fixture, run ``python -m scudo.scripts.cleanup_stale_cdao``
to drop legacy IRIs (Marketing / urn:cdao / bare cdao: prefixes) before re-seeding.

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


def _read_source(path: str) -> bytes:
    """Read fixture bytes from a local path or an ``s3://bucket/key`` URI.

    boto3 is imported lazily so offline import stays clean; the in-VPC CodeBuild
    seed step reads the catalogue straight from the cdao-catalog S3 bucket.
    """
    if path.startswith("s3://"):
        bucket, _, key = path[len("s3://") :].partition("/")
        if not bucket or not key:
            raise ValueError(f"malformed s3 uri {path!r} (need s3://bucket/key)")
        import boto3  # lazy

        return boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
    with open(path, "rb") as fh:
        return fh.read()


def _coerce_ref(value: Any) -> Optional[str]:
    """A JSON-LD reference may be a bare IRI string or a {"@id": ...} object."""
    if isinstance(value, dict):
        value = value.get("@id")
    return str(value) if value else None


def _normalize_node(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalise a flat node ({iri,label,parent_iri}) OR a CDAO JSON-LD node
    ({@id, @type, label/prefLabel/title, inSubdomain/inDomain}) to the flat
    {iri,label,parent_iri} shape the store persists. Parent is the nearest
    container (subdomain before domain) so the taxonomy keeps its hierarchy."""
    iri = _coerce_ref(raw.get("iri") or raw.get("@id"))
    label = raw.get("label") or raw.get("prefLabel") or raw.get("title")
    parent = _coerce_ref(
        raw.get("parent_iri") or raw.get("inSubdomain") or raw.get("inDomain")
    )
    if not label and iri:
        # Derive a readable label from the last IRI segment as a last resort.
        label = iri.rsplit(":", 1)[-1].replace("-", " ").replace("_", " ").title()
    return {"iri": iri, "label": label, "parent_iri": parent}


def _load_fixture(path: str) -> list[dict[str, Any]]:
    """Load taxonomy node dicts from a JSON/JSON-LD fixture (local path or s3://).

    Accepts a top-level list, or an object with a ``nodes`` or ``@graph`` list.
    Each entry may be a flat {iri,label} node or a CDAO JSON-LD node; both are
    normalised. Raises a clear error on a malformed fixture rather than letting
    a cryptic KeyError surface downstream.
    """
    data = json.loads(_read_source(path).decode("utf-8"))
    if isinstance(data, dict):
        data = data.get("nodes") or data.get("@graph") or []
    if not isinstance(data, list):
        raise ValueError(
            f"Taxonomy fixture {path!r} must be a JSON list of node objects "
            f"(or an object with a 'nodes'/'@graph' list), "
            f"got {type(data).__name__}."
        )
    nodes: list[dict[str, Any]] = []
    for i, raw in enumerate(data):
        if not isinstance(raw, dict):
            raise ValueError(
                f"Taxonomy fixture {path!r} entry {i} is not an object: {raw!r}"
            )
        node = _normalize_node(raw)
        if not node.get("iri") or not node.get("label"):
            raise ValueError(
                f"Taxonomy fixture {path!r} entry {i} is missing an IRI "
                f"(iri/@id) and/or label: {raw!r}"
            )
        nodes.append(node)
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
    """Replace the taxonomy in one store operation. Returns the node count.

    ``store`` is any ``RetrievalStore`` (the FalkorDB store in deployment).
    Kept dependency-light so a caller/test can pass an in-memory store.
    """
    taxonomy = [_to_taxonomy_node(raw) for raw in nodes]
    store.replace_taxonomy(taxonomy)
    return len(taxonomy)


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
        default_fixture = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "fixtures",
            "cdao_catalogue.json",
        )
        if os.path.isfile(default_fixture):
            nodes = _load_fixture(default_fixture)
            source = f"fixture {default_fixture!r}"
        else:
            nodes = list(_DEFAULT_NODES)
            source = "built-in default CDAO node set"

    # LAZY import of config + store factory so offline import stays clean and
    # the falkordb client is only required at run time.
    from scudo_mapping_mcp.config import settings
    from scudo_mapping_mcp.store import close_store, get_store

    if settings.store_backend not in {"falkordb", "scipy_sqlite"}:
        print(
            f"[seed_falkordb] ERROR: STORE_BACKEND={settings.store_backend!r}, "
            f"expected 'falkordb' or 'scipy_sqlite'. Set a supported matching "
            f"backend so the seed lands in the store the matcher reads.",
            file=sys.stderr,
        )
        return 2

    if settings.store_backend == "falkordb":
        destination = (
            f"url={_redact_url(settings.falkordb_url)} graph={settings.graph_name!r}"
        )
    else:
        destination = f"path={settings.scipy_sqlite_path!r}"
    print(
        f"[seed_falkordb] backend={settings.store_backend} {destination} "
        f"source={source} nodes={len(nodes)}"
    )

    # Construct the store via the factory. The factory caches; reset first so a
    # stale handle from an earlier import in the same process is not reused.
    close_store()
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
            f"[seed_falkordb] ERROR: could not construct the matching store "
            f"({destination}): {exc}",
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
    if not healthy and not (
        settings.store_backend == "scipy_sqlite" and store.taxonomy_size() == 0
    ):
        print(
            f"[seed_falkordb] ERROR: matching store ({destination}) is "
            f"unreachable or unhealthy (health check failed).",
            file=sys.stderr,
        )
        close_store()
        return 3

    try:
        upserted = seed(nodes, store)
    except Exception as exc:
        print(f"[seed_falkordb] ERROR: upsert failed: {exc}", file=sys.stderr)
        close_store()
        return 4
    finally:
        # close() swallows its own errors; safe to call even after success.
        pass

    close_store()
    print(
        f"[seed_falkordb] DONE: {upserted} taxonomy node(s) upserted into "
        f"{settings.store_backend} ({destination})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
