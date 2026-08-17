"""Authoritative-Neptune endpoint config.

The Bedrock factories are shared (scudo.shared.bedrock). The graph endpoint
is authoritative-specific — a separate cluster from the lexical sidecar.

Env:
  NEPTUNE_ENDPOINT          authoritative cluster endpoint
  NEPTUNE_PORT              default 8182
  NEPTUNE_USE_IAM_AUTH      default true
"""
from __future__ import annotations

import os

_DEFAULT_PORT = "8182"


def _env(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, default)


def neptune_endpoint() -> str:
    ep = _env("NEPTUNE_ENDPOINT")
    if not ep:
        raise RuntimeError(
            "NEPTUNE_ENDPOINT not set — caller should use the mock path "
            "(scudo.authoritative.mock) instead of constructing a real client."
        )
    return ep


def neptune_port() -> int:
    return int(_env("NEPTUNE_PORT", _DEFAULT_PORT) or _DEFAULT_PORT)


def use_iam_auth() -> bool:
    return _env("NEPTUNE_USE_IAM_AUTH", "true").lower() in ("1", "true", "yes")


__all__ = ["neptune_endpoint", "neptune_port", "use_iam_auth"]
