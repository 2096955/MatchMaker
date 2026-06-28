"""Remove stale CDAO taxonomy nodes from FalkorDB after catalogue migration.

Deletes ``TaxonomyNode`` rows whose IRI uses legacy or fabricated schemes:

  * contains ``Marketing`` (incoherent branch from early PoC fixtures)
  * contains ``urn:cdao`` (fabricated URN scheme)
  * bare ``cdao:`` prefix (legacy short IRIs, not ``jpmorgan:data:cdao:…``)

Connects through the same store factory as ``seed_falkordb`` (``get_store`` with
``STORE_BACKEND=falkordb``, ``FALKORDB_URL``, ``GRAPH_NAME``).

Run (DRY-RUN by default — pass ``--apply`` to actually delete)::

    # Dry-run: report what WOULD be deleted, change nothing.
    STORE_BACKEND=falkordb \\
    FALKORDB_URL=falkordb://falkordb.scudo.local:6379 \\
    python -m scudo.scripts.cleanup_stale_cdao

    # Destructive: actually DETACH DELETE the stale nodes.
    STORE_BACKEND=falkordb \\
    FALKORDB_URL=falkordb://falkordb.scudo.local:6379 \\
    python -m scudo.scripts.cleanup_stale_cdao --apply

Heavy dependencies are imported lazily inside ``main`` so importing this module
offline does not require the ``falkordb`` client or a live database.
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional


def is_stale_cdao_iri(iri: str) -> bool:
    """Return True if ``iri`` is a legacy or fabricated taxonomy node."""
    if "Marketing" in iri:
        return True
    if "urn:cdao" in iri:
        return True
    if iri.startswith("cdao:"):
        return True
    return False


def find_stale_iris(store) -> list[str]:
    """List IRIs of taxonomy nodes that match the stale CDAO patterns."""
    return [
        node.iri
        for node in store.list_taxonomy_nodes()
        if node.iri and is_stale_cdao_iri(node.iri)
    ]


def delete_stale_nodes(store, iris: list[str]) -> int:
    """Detach-delete stale taxonomy nodes. Returns the number deleted."""
    if not iris:
        return 0
    graph = store._g
    for iri in iris:
        graph.query(
            "MATCH (t:TaxonomyNode {iri:$iri}) DETACH DELETE t",
            {"iri": iri},
        )
    return len(iris)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Delete stale CDAO taxonomy nodes from FalkorDB. "
        "DRY-RUN by default — pass --apply to actually delete."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete stale nodes. Without this flag the script only "
        "reports what it would delete (dry-run).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicit dry-run (the default). Report stale nodes without deleting.",
    )
    args = parser.parse_args(argv[1:])
    # Safe default: anything that is not an explicit --apply is a dry-run.
    args.dry_run = not args.apply
    return args


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entrypoint. Returns a process exit code (0 = success)."""
    argv = list(sys.argv if argv is None else argv)
    args = _parse_args(argv)

    from scudo.seed_falkordb import _redact_url
    from scudo_mapping_mcp.config import settings
    from scudo_mapping_mcp.store import get_store, reset_store_cache
    from scudo_mapping_mcp.store.falkordb_store import FalkorDBStore

    if settings.store_backend != "falkordb":
        print(
            f"[cleanup_stale_cdao] ERROR: STORE_BACKEND={settings.store_backend!r}, "
            f"expected 'falkordb'. Set STORE_BACKEND=falkordb to target FalkorDB.",
            file=sys.stderr,
        )
        return 2

    redacted_url = _redact_url(settings.falkordb_url)
    mode = "dry-run" if args.dry_run else "delete"
    print(
        f"[cleanup_stale_cdao] backend=falkordb url={redacted_url} "
        f"graph={settings.graph_name!r} mode={mode}"
    )

    reset_store_cache()
    try:
        store = get_store()
    except ImportError as exc:
        print(
            f"[cleanup_stale_cdao] ERROR: the 'falkordb' client is not installed: {exc}. "
            f"Install it (pip install falkordb) to run cleanup against a real instance.",
            file=sys.stderr,
        )
        return 3
    except Exception as exc:
        print(
            f"[cleanup_stale_cdao] ERROR: could not construct the FalkorDB store at "
            f"{redacted_url}: {exc}",
            file=sys.stderr,
        )
        return 3

    if not isinstance(store, FalkorDBStore):
        print(
            "[cleanup_stale_cdao] ERROR: store factory did not return FalkorDBStore.",
            file=sys.stderr,
        )
        store.close()
        return 2

    try:
        healthy = store.health()
    except Exception as exc:
        healthy = False
        print(
            f"[cleanup_stale_cdao] WARN: health check raised: {exc}",
            file=sys.stderr,
        )
    if not healthy:
        print(
            f"[cleanup_stale_cdao] FalkorDB at {redacted_url} is unavailable "
            f"(health check failed). No nodes deleted.",
            file=sys.stderr,
        )
        store.close()
        return 3

    try:
        stale_iris = find_stale_iris(store)
    except Exception as exc:
        print(
            f"[cleanup_stale_cdao] ERROR: could not list taxonomy nodes: {exc}",
            file=sys.stderr,
        )
        store.close()
        return 4

    if args.dry_run:
        print(
            f"[cleanup_stale_cdao] DRY-RUN: would delete {len(stale_iris)} "
            f"stale taxonomy node(s)."
        )
        for iri in stale_iris:
            print(f"  - {iri}")
        store.close()
        return 0

    print(
        f"[cleanup_stale_cdao] APPLY: deleting {len(stale_iris)} stale "
        f"taxonomy node(s) from graph {settings.graph_name!r} at {redacted_url}:"
    )
    for iri in stale_iris:
        print(f"  - {iri}")

    try:
        deleted = delete_stale_nodes(store, stale_iris)
    except Exception as exc:
        print(
            f"[cleanup_stale_cdao] ERROR: delete failed: {exc}",
            file=sys.stderr,
        )
        store.close()
        return 4

    store.close()
    print(
        f"[cleanup_stale_cdao] DONE: deleted {deleted} stale taxonomy node(s) "
        f"from graph {settings.graph_name!r} at {redacted_url}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
