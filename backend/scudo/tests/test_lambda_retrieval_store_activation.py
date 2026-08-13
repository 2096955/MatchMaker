from __future__ import annotations


def _payload() -> dict:
    return {"vendor": "LSEG", "vendor_product_ref": "P1"}


def test_scipy_sqlite_backend_alone_does_not_activate_retrieval_store(monkeypatch):
    from scudo import lambda_handler, matcher_bridge

    monkeypatch.setenv("STORE_BACKEND", "scipy_sqlite")
    monkeypatch.delenv("SCUDO_USE_FALKORDB", raising=False)
    monkeypatch.delenv("SCUDO_USE_RETRIEVAL_STORE", raising=False)
    called = False

    def retrieval(*args, **kwargs):
        nonlocal called
        called = True
        return [{"iri": "node:1", "label": "One", "score": 0.9}]

    monkeypatch.setattr(matcher_bridge, "retrieve_candidates", retrieval)

    lambda_handler._candidate_dicts(_payload(), {}, "one")
    assert called is False


def test_neutral_flag_activates_falkordb_backend(monkeypatch):
    from scudo import lambda_handler, matcher_bridge

    monkeypatch.setenv("STORE_BACKEND", "falkordb")
    monkeypatch.setenv("SCUDO_USE_RETRIEVAL_STORE", "1")
    monkeypatch.delenv("SCUDO_USE_FALKORDB", raising=False)
    monkeypatch.setattr(matcher_bridge, "retrieval_store_ready", lambda: (True, None))
    monkeypatch.setattr(
        matcher_bridge,
        "retrieve_candidates",
        lambda vendor_product, term="", limit=10: [
            {"iri": "node:2", "label": "Two", "score": 0.8}
        ],
    )

    assert lambda_handler._candidate_dicts(_payload(), {}, "two")[0]["iri"] == "node:2"


def test_explicit_scipy_sqlite_with_empty_taxonomy_fails_clear(monkeypatch):
    from scudo import lambda_handler, matcher_bridge

    monkeypatch.setenv("STORE_BACKEND", "scipy_sqlite")
    monkeypatch.setenv("SCUDO_USE_RETRIEVAL_STORE", "true")
    monkeypatch.delenv("SCUDO_USE_FALKORDB", raising=False)
    monkeypatch.setattr(
        matcher_bridge, "retrieval_store_ready", lambda: (False, "empty")
    )

    try:
        lambda_handler._candidate_dicts(_payload(), {}, "one")
    except RuntimeError as exc:
        assert "not ready" in str(exc)
        assert "empty" in str(exc)
    else:
        raise AssertionError("empty retrieval store must fail closed")


def test_explicit_preseeded_scipy_sqlite_retrieves(monkeypatch):
    from scudo import lambda_handler, matcher_bridge

    monkeypatch.setenv("STORE_BACKEND", "scipy_sqlite")
    monkeypatch.setenv("SCUDO_USE_RETRIEVAL_STORE", "1")
    monkeypatch.setattr(matcher_bridge, "retrieval_store_ready", lambda: (True, None))
    monkeypatch.setattr(
        matcher_bridge,
        "retrieve_candidates",
        lambda vendor_product, term="", limit=10: [
            {"iri": "node:3", "label": "Three", "score": 0.7}
        ],
    )

    assert (
        lambda_handler._candidate_dicts(_payload(), {}, "three")[0]["iri"] == "node:3"
    )
