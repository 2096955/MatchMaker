"""REST API routes for the mock iFusion publish seam (``/api/ifusion``).

Architecture role: the dataflow diagram shows NEP --> IFU (the persisted
canonical mapping is published downstream to iFusion over SPI V2). The real
iFusion wiring is out of scope for the demo, so this blueprint stands in a
deterministic mock that lets the UI complete the round-trip visibly.

Trust posture (READ THIS BEFORE WIRING ANYTHING REAL):

  Publish happens AFTER Persistence has written to Neptune+DynamoDB; the
  HMAC seal has already been verified by the Persistence MCP at I5. By the
  time a request lands here the canonical mapping is, by construction,
  considered durable. This endpoint therefore does NOT re-verify the seal
  and does NOT call any of the three trust-graded MCPs — it is a pure
  outbound side-effect that today happens to be a no-op.

  When the real SPI V2 client lands, replace the body of ``_do_publish``
  with the transport call; the request/response contract on this blueprint
  stays the same so the UI does not move.

Endpoints:

- ``POST /api/ifusion/publish`` — pretend to publish one canonical mapping.
  Returns a synthetic ``publish_id`` so the UI has something concrete to
  display. Idempotent on ``(vendor, product_id, mapping_id)``: a second
  call with the same tuple returns the original ``publish_id`` and
  ``status='duplicate'`` so the demo can replay without confusing the
  reviewer.

- ``GET  /api/ifusion/recent`` — return the most recent N publishes
  (default 20, max 100) so the UI can render a "what's gone out" panel.

The in-memory store is process-local and resets on restart — that is
intentional for the demo. The real version writes to DynamoDB via the
Persistence MCP; the swap point is ``_RECENT`` and ``_INDEX``.
"""

from __future__ import annotations

import itertools
import threading
import time
from collections import OrderedDict
from typing import Any

from flask import Blueprint, jsonify, request

from logger import ui_logger

ifusion_bp = Blueprint('ifusion', __name__)

# ── In-memory mock store ─────────────────────────────────────────────────
# _INDEX maps (vendor, product_id, mapping_id) -> publish_id for idempotency.
# _RECENT is an OrderedDict of publish_id -> record so we can return them
# in publish order without sorting on every read. Both are guarded by a
# single lock — concurrent Flask workers each get their own process and so
# their own copy, but per-process we still want correct behaviour under the
# dev server's threaded mode.
_LOCK = threading.Lock()
_INDEX: dict[tuple[str, str, str], str] = {}
_RECENT: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
# Monotonic publish counter so publish_ids are visually ordered for the demo.
_COUNTER = itertools.count(1)

# How many records we keep in memory. Old records fall off the back so a
# long-running demo doesn't grow unbounded.
_MAX_RETAINED = 500
# Default and max for GET /recent.
_RECENT_DEFAULT = 20
_RECENT_MAX = 100

_CHANNEL = 'spi_v2'


def _do_publish(vendor: str, product_id: str, mapping_id: str) -> dict[str, Any]:
    """Pretend to publish to iFusion over SPI V2.

    Real implementation would call the SPI V2 client here and translate its
    response onto the record dict. The mock just synthesises a publish_id
    and stamps a wall-clock timestamp so the UI can render something.

    Returns the record dict ready to be inserted into ``_RECENT``.
    """
    n = next(_COUNTER)
    ts = int(time.time())
    return {
        'publish_id': f'mock-pub-{ts}-{n}',
        'channel': _CHANNEL,
        'status': 'accepted',
        'vendor': vendor,
        'product_id': product_id,
        'mapping_id': mapping_id,
        'published_at': ts,
    }


def _trim():
    """Evict the oldest records once retention is exceeded. Caller holds the lock."""
    while len(_RECENT) > _MAX_RETAINED:
        evicted_id, evicted = _RECENT.popitem(last=False)
        # Also drop the idempotency entry so a re-publish gets a fresh id;
        # this matches the eventual DynamoDB TTL semantics.
        key = (evicted['vendor'], evicted['product_id'], evicted['mapping_id'])
        if _INDEX.get(key) == evicted_id:
            del _INDEX[key]


@ifusion_bp.post('/ifusion/publish')
def publish_mapping():
    """Publish one canonical mapping to the mock iFusion channel.

    JSON body:
        mapping_id (str): Identifier for the persisted canonical mapping.
        vendor (str):     Vendor whose product was mapped.
        product_id (str): Vendor-native product id.

    Returns:
        flask.Response: JSON record. On a fresh publish:
            ``{published: True, publish_id, channel, status='accepted',
               vendor, product_id, mapping_id, published_at}``.
            On a replay with the same (vendor, product_id, mapping_id):
            same ``publish_id``, ``status='duplicate'``.
    """
    body = request.get_json(silent=True) or {}
    vendor = (body.get('vendor') or '').strip()
    product_id = (body.get('product_id') or '').strip()
    mapping_id = (body.get('mapping_id') or '').strip()

    if not vendor:
        return jsonify({'error': 'vendor is required'}), 400
    if not product_id:
        return jsonify({'error': 'product_id is required'}), 400
    if not mapping_id:
        return jsonify({'error': 'mapping_id is required'}), 400

    key = (vendor, product_id, mapping_id)
    with _LOCK:
        existing_id = _INDEX.get(key)
        if existing_id is not None and existing_id in _RECENT:
            # Replay: return the original record but flip status so the UI can
            # show "already published" without the reviewer thinking it's a
            # fresh acceptance.
            record = dict(_RECENT[existing_id])
            record['status'] = 'duplicate'
            ui_logger.info(
                'iFusion publish replayed (mock)',
                vendor=vendor, product_id=product_id, mapping_id=mapping_id,
                publish_id=existing_id,
            )
            return jsonify({'published': True, **record})

        record = _do_publish(vendor, product_id, mapping_id)
        _RECENT[record['publish_id']] = record
        _INDEX[key] = record['publish_id']
        _trim()

    ui_logger.info(
        'iFusion publish accepted (mock)',
        vendor=vendor, product_id=product_id, mapping_id=mapping_id,
        publish_id=record['publish_id'], channel=record['channel'],
    )
    return jsonify({'published': True, **record})


@ifusion_bp.get('/ifusion/recent')
def list_recent_publishes():
    """Return the most recent publishes (newest first).

    Query params:
        limit (int): 1-100, default 20.

    Returns:
        flask.Response: JSON ``{count, publishes: [...]}``. Each entry has
            the same shape as the publish response (minus the ``published``
            flag — caller knows it's a publish list).
    """
    try:
        limit = int(request.args.get('limit', _RECENT_DEFAULT))
    except (TypeError, ValueError):
        return jsonify({'error': 'limit must be an integer'}), 400
    if not 1 <= limit <= _RECENT_MAX:
        return jsonify({'error': f'limit must be between 1 and {_RECENT_MAX}'}), 400

    with _LOCK:
        # _RECENT is insertion-ordered; reverse to get newest-first.
        records = list(reversed(_RECENT.values()))[:limit]
        # Defensive copies so callers can't mutate our internal state.
        payload = [dict(r) for r in records]

    return jsonify({'count': len(payload), 'publishes': payload})


# ── Test seam ─────────────────────────────────────────────────────────────
def _reset_for_tests():
    """Clear the in-memory mock state. Used by the smoke gate, not the UI."""
    with _LOCK:
        _INDEX.clear()
        _RECENT.clear()
