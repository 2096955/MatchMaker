"""Bootstrap the SCUDO paid data stores after CloudFormation creates them.

This script is run by the VPC-attached CodeBuild project. It creates the
Aurora tables, loads the CDAO catalogue into Neptune, and builds the initial
OpenSearch lexical/vector index. It is idempotent and can be re-run after a
stack update or catalogue refresh.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from .seed_falkordb import _normalize_node


def _fixture_arg(argv: list[str]) -> str | None:
    if len(argv) > 1 and argv[1].strip():
        return argv[1].strip()
    env = os.environ.get("SCUDO_TAXONOMY_SEED", "").strip()
    return env or None


def _load_nodes(path: str | None) -> list[dict[str, Any]]:
    if not path:
        raise ValueError("expected a CDAO fixture path or SCUDO_TAXONOMY_SEED")
    data = json.loads(_load_fixture_source(path).decode("utf-8"))
    graph = data.get("@graph") if isinstance(data, dict) else data
    if not isinstance(graph, list):
        raise ValueError(f"CDAO fixture {path!r} must contain a list or @graph list")
    return [_normalize_node(raw) for raw in graph if isinstance(raw, dict)]


def _load_fixture_source(path: str) -> bytes:
    if path.startswith("s3://"):
        bucket, _, key = path[len("s3://") :].partition("/")
        if not bucket or not key:
            raise ValueError(f"malformed s3 uri {path!r}")
        import boto3

        return boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
    with open(path, "rb") as fh:
        return fh.read()


def _init_aurora() -> None:
    from .projection_handler import _ensure_aurora_schema

    _ensure_aurora_schema()
    if os.environ.get("SCUDO_AURORA_CLUSTER_ARN"):
        from .aurora_store import ensure_schema as _ensure_aurora_store_schema

        _ensure_aurora_store_schema()
        print("[init_data_platform] Aurora schema ready")
    else:
        print("[init_data_platform] Aurora not configured; skipped")


def _seed_neptune(nodes: list[dict[str, Any]]) -> None:
    endpoint = (
        os.environ.get("SCUDO_NEPTUNE_ENDPOINT")
        or os.environ.get("NEPTUNE_ENDPOINT")
        or ""
    ).strip()
    if not endpoint:
        print("[init_data_platform] Neptune not configured; skipped")
        return
    from scudo_mapping_mcp.models import TaxonomyNode
    from scudo_mapping_mcp.store.neptune_store import NeptuneStore

    store = NeptuneStore(
        endpoint=endpoint, graph_name=os.environ.get("GRAPH_NAME", "scudo_mapping")
    )
    if not store.health():
        raise RuntimeError(f"Neptune endpoint {endpoint!r} is not healthy")
    count = 0
    for raw in nodes:
        if not raw.get("iri") or not raw.get("label"):
            continue
        store.upsert_taxonomy_node(
            TaxonomyNode(
                iri=str(raw["iri"]),
                label=str(raw["label"]),
                parent_iri=raw.get("parent_iri") or None,
            )
        )
        count += 1
    store.close()
    print(f"[init_data_platform] Neptune CDAO nodes upserted: {count}")


def _seed_opensearch(nodes: list[dict[str, Any]]) -> None:
    endpoint = os.environ.get("SCUDO_OPENSEARCH_ENDPOINT", "").strip()
    if not endpoint:
        print("[init_data_platform] OpenSearch not configured; skipped")
        return
    from .projection_handler import (
        _ensure_opensearch_index,
        _opensearch_request,
        _titan_embedding,
    )

    _ensure_opensearch_index()
    index = os.environ.get("SCUDO_OPENSEARCH_INDEX", "scudo-catalog")
    count = 0
    for raw in nodes:
        iri = raw.get("iri")
        if not iri:
            continue
        label = str(raw.get("label") or "")
        parent = raw.get("parent_iri") or ""
        text = " ".join(x for x in [label, iri, parent] if x)
        doc = {
            "doc_type": "cdao_node",
            "vendor": "",
            "vendor_product_ref": "",
            "vendor_product_iri": "",
            "target_iri": iri,
            "graph_iri": "https://jpmc.example/mds/graphs/scudo_mapping",
            "label": label,
            "description": parent,
            "payload": raw,
        }
        embedding = _titan_embedding(text)
        if embedding:
            doc["embedding"] = embedding
        doc_id = str(iri).replace("/", "%2F").replace("#", "%23")
        _opensearch_request("PUT", f"/{index}/_doc/{doc_id}", doc)
        count += 1
    print(f"[init_data_platform] OpenSearch CDAO docs indexed: {count}")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    try:
        nodes = _load_nodes(_fixture_arg(argv))
        _init_aurora()
        _seed_neptune(nodes)
        _seed_opensearch(nodes)
    except Exception as exc:
        print(f"[init_data_platform] ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
