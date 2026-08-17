"""FalkorDB candidate retrieval — retrieve_candidates only."""

from __future__ import annotations


class BridgeError(RuntimeError):
    pass


def retrieve_candidates(
    vendor_product: dict, *, term: str = "", limit: int = 10
) -> list[dict]:
    """Return [{iri, label, score}] from FalkorDB nominator. Raises BridgeError."""
    try:
        from scudo_mapping_mcp.config import settings as _settings
        from scudo_mapping_mcp.models import VendorProductRef as _VendorProductRef
        from scudo_mapping_mcp.store import get_store as _get_store
    except Exception as exc:
        raise BridgeError(f"scudo_mapping_mcp unavailable: {exc!r}") from exc

    backend = (_settings.store_backend or "").strip().lower()
    if backend != "falkordb":
        raise BridgeError(f"STORE_BACKEND={backend!r}; need falkordb")

    try:
        store = _get_store()
    except Exception as exc:
        raise BridgeError(f"FalkorDB store init failed: {exc!r}") from exc

    vp = vendor_product or {}
    vendor = str(vp.get("vendor") or "").strip()
    product_id = str(
        vp.get("product_id")
        or vp.get("vendor_product_ref")
        or vp.get("id")
        or vp.get("ref")
        or ""
    ).strip()
    if not vendor or not product_id:
        raise BridgeError("vendor_product needs vendor + product id")

    name = str(vp.get("name") or vp.get("title") or term or "").strip()
    raw = vp.get("raw")
    try:
        ref = _VendorProductRef(
            vendor=vendor,
            product_id=product_id,
            name=name,
            description=str(vp.get("description") or "").strip(),
            raw=raw if isinstance(raw, dict) else {},
        )
        cands = store.find_similar_products(ref, max_results=limit)
    except Exception as exc:
        raise BridgeError(f"retrieval failed: {exc!r}") from exc

    out: list[dict] = []
    for cand in cands or []:
        node = getattr(cand, "node", None)
        iri, label = getattr(node, "iri", None), getattr(node, "label", None)
        if not iri or not label:
            continue
        try:
            sim = max(0.0, min(1.0, float(getattr(cand, "similarity", 0.0))))
        except (TypeError, ValueError):
            sim = 0.0
        out.append({"iri": iri, "label": label, "score": sim})
    return out
