"""SCUDO visibility surface — /api/visibility/*.

SCUDO is the visibility platform for the backend, so the routes that
expose live operational state of the three-MCP trust gradient live here
rather than under ``admin_bp`` (which is for human-administered roles
and users).

Endpoints
---------
GET /api/visibility/mcp-host
    Returns the McpHost.metrics() snapshot, or ``{"enabled": false}``
    when the host hasn't been wired (the default in this sandbox build
    until WS-C lands an HTTP transport for the three MCPs).

The metrics shape is documented on McpHost.metrics() — kept there so
the schema is co-located with the producer.
"""
from flask import Blueprint, jsonify

from scudo_mapping_mcp.mcp_host import get_host


visibility_bp = Blueprint("visibility", __name__)


@visibility_bp.get("/visibility/mcp-host")
def mcp_host_metrics():
    """Return the live McpHost metrics snapshot.

    When SCUDO_MCP_HOST_ENABLED is unset (the sandbox default) the host
    singleton is None and we return ``{"enabled": false}`` so the admin
    UI can show a clear "off" state instead of an empty graph.
    """
    host = get_host()
    if host is None:
        return jsonify({"enabled": False})
    return jsonify(host.metrics())
