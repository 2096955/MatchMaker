"""Bedrock / Anthropic model id + region helpers.

Default Bedrock LLM is Claude Opus 4.8 via the US cross-region inference
profile — same pin as Capone ``backend/scudo/shared/bedrock.py``. Bare
``anthropic.claude-opus-4-8`` is rejected on on-demand for 4.x Claudes.

Anthropic-compatible endpoints (local shim router, LiteLLM, etc.) use the
API id ``claude-opus-4-8`` via ``anthropic_llm_id()``.
"""

from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_REGION = "us-east-1"
_DEFAULT_LLM = "us.anthropic.claude-opus-4-8"
_DEFAULT_ANTHROPIC_LLM = "claude-opus-4-8"
_DEFAULT_ROUTER_KEY = Path.home() / ".codex" / "shim-router" / "router.key"


def aws_region() -> str:
    return (
        os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or _DEFAULT_REGION
    )


def bedrock_llm_id() -> str:
    """Opus 4.8 unless overridden (``SCUDO_BEDROCK_LLM_ID`` or ``BEDROCK_LLM_MODEL_ID``)."""
    return (
        os.environ.get("SCUDO_BEDROCK_LLM_ID")
        or os.environ.get("BEDROCK_LLM_MODEL_ID")
        or _DEFAULT_LLM
    )


def anthropic_llm_id() -> str:
    """Anthropic Messages API model id for Opus 4.8 (shim / direct Anthropic)."""
    raw = (
        os.environ.get("SCUDO_ANTHROPIC_MODEL_ID")
        or os.environ.get("SCUDO_BEDROCK_LLM_ID")
        or _DEFAULT_ANTHROPIC_LLM
    )
    if raw.startswith("us.anthropic."):
        raw = raw.removeprefix("us.anthropic.")
    if raw.startswith("anthropic."):
        raw = raw.removeprefix("anthropic.")
    return raw


def anthropic_client_args() -> dict:
    """Client kwargs for ``strands.models.anthropic.AnthropicModel``."""
    args: dict = {}
    base = os.environ.get("ANTHROPIC_BASE_URL") or os.environ.get(
        "SCUDO_ANTHROPIC_BASE_URL"
    )
    if base:
        args["base_url"] = base
    key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get(
        "SCUDO_ANTHROPIC_API_KEY"
    )
    if not key and _DEFAULT_ROUTER_KEY.is_file():
        key = _DEFAULT_ROUTER_KEY.read_text(encoding="utf-8").strip()
    if key:
        args["api_key"] = key
    return args
