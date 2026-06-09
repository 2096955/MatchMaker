"""Vendor-Catalogue MCP — mock-backed implementation of the v0.1 contract.

The package is the load-bearing contract surface. `server` holds the @mcp.tool
signatures and Pydantic models; `mock_backend` is the single seam swapped for
S3/Glue on JPMC AWS.
"""
