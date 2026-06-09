"""Shared AWS client primitives used by both the authoritative-graph and the
lexical-sidecar subpackages.

Keeps Bedrock client construction in one place so model id/region overrides
flow through a single env-var surface, regardless of which store the caller
is pointed at.
"""
from .bedrock import (
    MissingDependencyError,
    aws_region,
    bedrock_embed_id,
    bedrock_llm_id,
    clear_cache,
    get_bedrock_embedding,
    get_bedrock_llm,
)

__all__ = [
    "MissingDependencyError",
    "aws_region", "bedrock_llm_id", "bedrock_embed_id",
    "get_bedrock_llm", "get_bedrock_embedding",
    "clear_cache",
]
