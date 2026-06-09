"""REST API routes for the vendor catalogue (``/api/catalogue``).

Thin HTTP facade over the ``vendor_catalogue_mcp`` package:

- ``GET /api/catalogue/products``                        — list (cursor-paged)
- ``GET /api/catalogue/products/<vendor>/<vendor_ref>``  — one normalised product
- ``GET /api/catalogue/schema``                          — declared field surface
- ``GET /api/catalogue/delta``                           — added/updated since wm

These endpoints share ``contract.py`` and ``mock_backend.py`` with the MCP
server in ``vendor_catalogue_mcp/server.py``; only the transport differs (HTTP
for the browser, stdio MCP for AI/automation clients). On JPMC AWS cutover,
``mock_backend._read_vendor_frame`` swaps from local parquet to S3/Glue and
neither transport changes.
"""

from datetime import datetime

from flask import Blueprint, jsonify, request

from logger import ui_logger
from vendor_catalogue_mcp import mock_backend
from vendor_catalogue_mcp.contract import VendorId

catalogue_bp = Blueprint('catalogue', __name__)

# Public — used by the frontend's vendor dropdown to avoid duplicating the enum.
_VENDORS = [v.value for v in VendorId]


def _parse_vendor(raw):
    """Resolve a query/path string to a ``VendorId`` (defaults to LSEG)."""
    if raw is None or raw == '':
        return VendorId.LSEG
    try:
        return VendorId(raw.lower())
    except ValueError:
        raise ValueError(f"unknown vendor {raw!r} (valid: {', '.join(_VENDORS)})")


def _parse_iso_datetime(raw):
    """ISO-8601 → tz-aware datetime; trailing Z is normalised to +00:00."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError(f"must be ISO-8601, got {raw!r}")


@catalogue_bp.get('/catalogue/vendors')
def list_vendors():
    """Return the vendor enum so the UI dropdown stays in sync with the contract."""
    return jsonify({'vendors': _VENDORS, 'default': VendorId.LSEG.value})


@catalogue_bp.get('/catalogue/products')
def list_products():
    """List a vendor's catalogue product refs.

    Query params:
        vendor (str): VendorId value (default ``lseg``).
        cursor (str): Opaque cursor from a previous ``next_cursor``.
        limit (int): 1-200, default 50.
        modified_since (str): ISO-8601 timestamp; rows with ``modified_at``
            strictly greater are returned.

    Returns:
        flask.Response: JSON ``{items: [ProductRef], next_cursor: str|null}``.
    """
    try:
        vendor = _parse_vendor(request.args.get('vendor'))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    cursor = request.args.get('cursor') or None
    try:
        limit = int(request.args.get('limit', 50))
    except ValueError:
        return jsonify({'error': 'limit must be an integer'}), 400
    if not 1 <= limit <= 200:
        return jsonify({'error': 'limit must be between 1 and 200'}), 400

    try:
        modified_since = _parse_iso_datetime(request.args.get('modified_since'))
    except ValueError as e:
        return jsonify({'error': f'modified_since {e}'}), 400

    try:
        page = mock_backend.list_products(
            vendor=vendor, cursor=cursor, limit=limit, modified_since=modified_since,
        )
    except FileNotFoundError as e:
        # No parquet for this vendor yet — mock-only condition, surface clearly.
        ui_logger.warning('Catalogue parquet missing', vendor=vendor.value, error=str(e))
        return jsonify({'error': str(e)}), 503
    except ValueError as e:
        # Malformed cursor.
        return jsonify({'error': str(e)}), 400

    return jsonify(page.model_dump(mode='json'))


@catalogue_bp.get('/catalogue/products/<vendor>/<vendor_product_ref>')
def get_product(vendor, vendor_product_ref):
    """Return one normalised product (incl. provenance + raw_attributes)."""
    try:
        v = _parse_vendor(vendor)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    try:
        product = mock_backend.get_product(
            vendor=v, vendor_product_ref=vendor_product_ref,
        )
    except LookupError as e:
        return jsonify({'error': str(e)}), 404
    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 503
    return jsonify(product.model_dump(mode='json'))


@catalogue_bp.get('/catalogue/schema')
def get_schema():
    """Return the vendor's declared catalogue field surface."""
    try:
        v = _parse_vendor(request.args.get('vendor'))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    try:
        schema = mock_backend.get_schema(vendor=v)
    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 503
    return jsonify(schema.model_dump(mode='json'))


@catalogue_bp.get('/catalogue/delta')
def get_delta():
    """Return products added/updated/removed since a watermark (exclusive).

    Query params:
        vendor (str): VendorId value (default ``lseg``).
        since (str): ISO-8601 timestamp or ``{vendor}-<ISO-8601>`` form as
            returned by a previous ``next_watermark``.
    """
    try:
        v = _parse_vendor(request.args.get('vendor'))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    since = request.args.get('since')
    if not since:
        return jsonify(
            {'error': 'since is required (ISO-8601 or vendor-prefixed watermark)'}
        ), 400

    try:
        result = mock_backend.get_delta(vendor=v, since=since)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 503

    return jsonify(result.model_dump(mode='json'))
