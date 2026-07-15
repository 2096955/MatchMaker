"""Phase C — extractor allowed-keys contract accepts nullable/sequence_number.

The spec's "extractor asymmetry" note: conceptual_layer.json fixtures carry
``nullable`` (and now ``sequence_number``) but the extractor's validation
loop used to drop everything except vendor_field_name/label/data_type/
primary_key. Phase C extends the ACCEPTANCE side only: the live prompt text
(``_EXTRACT_SYSTEM_PROMPT``) is unchanged — the model is not asked to emit
the new keys, but when it does they are validated and carried instead of
silently dropped.
"""

from __future__ import annotations

from scudo_mapping_mcp.enrichment import _EXTRACT_SYSTEM_PROMPT, _validated_fields


def _proposed(**extra) -> list[dict]:
    return [
        {
            "vendor_field_name": "ticker",
            "label": "Ticker",
            "data_type": "string",
            "primary_key": True,
            **extra,
        }
    ]


def test_validated_fields_accepts_nullable_and_sequence_number():
    out = _validated_fields(
        _proposed(nullable=False, sequence_number=3), valid_keys={"ticker"}
    )
    assert out == [
        {
            "vendor_field_name": "ticker",
            "label": "Ticker",
            "data_type": "string",
            "primary_key": True,
            "nullable": False,
            "sequence_number": 3,
        }
    ]


def test_validated_fields_omits_new_keys_when_absent():
    out = _validated_fields(_proposed(), valid_keys={"ticker"})
    assert out[0]["nullable"] is None
    assert out[0]["sequence_number"] is None


def test_validated_fields_coerces_bad_new_key_values_to_none():
    """Off-contract values (non-bool nullable, non-int sequence_number) are
    coerced to None rather than admitted — same fail-closed discipline as
    data_type coercion to 'string'."""
    out = _validated_fields(
        _proposed(nullable="yes", sequence_number="first"), valid_keys={"ticker"}
    )
    assert out[0]["nullable"] is None
    assert out[0]["sequence_number"] is None


def test_validated_fields_still_rejects_off_list_names():
    out = _validated_fields(
        _proposed(vendor_field_name="invented"), valid_keys={"ticker"}
    )
    assert out == []


def test_live_prompt_text_unchanged_by_allowed_keys_extension():
    """The Phase C extension is acceptance-side only: the system prompt must
    NOT start advertising the new keys (that would change live Bedrock
    behaviour — explicitly out of scope per the spec's risk note)."""
    assert "nullable" not in _EXTRACT_SYSTEM_PROMPT
    assert "sequence_number" not in _EXTRACT_SYSTEM_PROMPT
    assert "sequenceNumber" not in _EXTRACT_SYSTEM_PROMPT


def test_extract_field_structure_carries_new_keys_onto_nodes(monkeypatch):
    """End-to-end through extract_field_structure: a validated field dict
    with nullable/sequence_number lands on the ConceptualNode."""
    import dataclasses

    from scudo_mapping_mcp import enrichment as enrichment_mod
    from scudo_mapping_mcp.models import ConceptualNodeKind, VendorProductRef

    monkeypatch.setattr(
        enrichment_mod,
        "settings",
        dataclasses.replace(enrichment_mod.settings, enrichment_backend="opus"),
    )
    monkeypatch.setattr(
        enrichment_mod,
        "_extract_invoke",
        lambda ref: [
            {
                "vendor_field_name": "ticker",
                "label": "Ticker",
                "data_type": "string",
                "primary_key": True,
                "nullable": False,
                "sequence_number": 2,
            }
        ],
    )
    ref = VendorProductRef(
        vendor="LSEG",
        product_id="EQ-PHASE-C",
        name="Equity Prices",
        raw={"ticker": "JPM"},
    )
    graph = enrichment_mod.extract_field_structure(ref, "jpmorgan:data:cdao:EQ")
    fields = [n for n in graph.nodes if n.kind == ConceptualNodeKind.FIELD]
    assert len(fields) == 1
    assert fields[0].nullable is False
    assert fields[0].sequence_number == 2
