"""Ingestion mock — single endpoint that fakes the M8 S3-reader seam.

For the demo loop we don't want to stand up a real ingestion pipeline (S3
landing zone + vendor adapters + audit-id minting + content-hash) just to
exercise the matcher. ``POST /api/mapping/ingestion-mock`` takes a hand-pasted
vendor row and returns a ``VendorProductRef``-shaped frame so the frontend
mapping demo can populate the working set without touching disk or network.

Trust gradient note: this route lives outside the three-MCP perimeter on
purpose. It DOES NOT persist anything, DOES NOT call the matcher, and DOES
NOT mint federated-audit identifiers — those are M8 work for the real S3
reader. The frame returned here has ``source_content_hash=None`` and
``source_file_audit_id=None`` so any HITL decision that flows from it is
clearly stamped "mock origin" in the precedent edge.

JSON body:
    vendor (str):       One of PRIORITY_VENDORS.
    product_id (str):   Vendor-native product id.
    name (str):         Display name (optional but typical).
    description (str):  Product description (optional).
    identifier (str):   Free-form extra id (ISIN / SEDOL / CUSIP / ticker).
                        Stored under VendorProductRef.raw so the matcher's
                        scope gate and feature extraction can still read it.

Returns:
    flask.Response: JSON ``VendorProductRef`` — the same shape the M8
        reader will hand the matcher once wired.
"""

from flask import Blueprint, jsonify, request
from pydantic import ValidationError

from logger import ui_logger
from scudo_mapping_mcp.config import PRIORITY_VENDORS
from scudo_mapping_mcp.models import VendorProductRef

ingestion_mock_bp = Blueprint('ingestion_mock', __name__)


@ingestion_mock_bp.post('/mapping/ingestion-mock')
def ingestion_mock():
    body = request.get_json(silent=True) or {}
    vendor = (body.get('vendor') or '').strip()
    product_id = (body.get('product_id') or '').strip()
    name = (body.get('name') or '').strip()
    description = (body.get('description') or '').strip()
    identifier = (body.get('identifier') or '').strip()

    if not vendor:
        return jsonify({'error': 'vendor is required'}), 400
    if not product_id:
        return jsonify({'error': 'product_id is required'}), 400
    if vendor not in PRIORITY_VENDORS:
        return jsonify({
            'error': (
                f"unknown vendor {vendor!r} "
                f"(valid: {', '.join(PRIORITY_VENDORS)})"
            ),
        }), 400

    raw = {}
    if identifier:
        raw['identifier'] = identifier

    try:
        ref = VendorProductRef(
            vendor=vendor,
            product_id=product_id,
            name=name,
            description=description,
            raw=raw,
            # Mock origin — no S3 audit id, no content hash. The real
            # M8 reader will populate these from the landed object.
            source_content_hash=None,
            source_file_audit_id=None,
        )
    except ValidationError as e:
        return jsonify({'error': e.errors()}), 400

    ui_logger.info(
        'Ingestion mock served',
        vendor=vendor, product_id=product_id,
        has_name=bool(name), has_desc=bool(description),
        has_identifier=bool(identifier),
    )
    payload = ref.model_dump(mode='json')
    payload['origin'] = 'mock'  # Demo-only field; not on the canonical shape.
    return jsonify(payload)
