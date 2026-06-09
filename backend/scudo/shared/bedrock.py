"""Shared Bedrock client factories.

Both the authoritative-graph workflow (publishing + confirmation reads) and
the sidecar workflow (lexical retrieval) hit the same Bedrock surface — the
same region, the same model access policies, the same credentials. Holding
them in one module avoids two slightly-different copies drifting.

Env:
  AWS_REGION                    default us-east-1
  BEDROCK_LLM_MODEL_ID          default a Sonnet placeholder; swap to Sonnet 4.6 on Atlas
  BEDROCK_EMBEDDING_MODEL_ID    default Titan v2
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

_DEFAULT_REGION = "us-east-1"
# Opus 4.8 is the strongest available Claude on Bedrock — best fit for the
# Mapping Specialist's high-stakes "no exact match" judgement per clarif. 83.
# Addressed via the us. cross-region inference profile because 4.x Claudes
# don't accept the bare model id on on-demand throughput. Swap to
# us.anthropic.claude-sonnet-4-6 to cut per-call cost ~5x if you're scaling
# the enrichment box to thousands of mappings/day.
_DEFAULT_LLM = "us.anthropic.claude-opus-4-8"
_DEFAULT_EMBED = "amazon.titan-embed-text-v2:0"


class MissingDependencyError(ImportError):
    """Raised when a Bedrock/LlamaIndex import fails — callers can fall back
    to mocks or surface the install hint."""


def _env(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, default)


def aws_region() -> str:
    return _env("AWS_REGION", _DEFAULT_REGION) or _DEFAULT_REGION


def bedrock_llm_id() -> str:
    return _env("BEDROCK_LLM_MODEL_ID", _DEFAULT_LLM)


def bedrock_embed_id() -> str:
    return _env("BEDROCK_EMBEDDING_MODEL_ID", _DEFAULT_EMBED)


@lru_cache(maxsize=1)
def get_bedrock_llm() -> Any:
    try:
        from llama_index.llms.bedrock import Bedrock
    except ImportError as e:
        raise MissingDependencyError("pip install llama-index-llms-bedrock") from e
    return Bedrock(model=bedrock_llm_id(), region_name=aws_region())


@lru_cache(maxsize=1)
def get_bedrock_embedding() -> Any:
    try:
        from llama_index.embeddings.bedrock import BedrockEmbedding
    except ImportError as e:
        raise MissingDependencyError("pip install llama-index-embeddings-bedrock") from e
    return BedrockEmbedding(model_name=bedrock_embed_id(), region_name=aws_region())


def clear_cache() -> None:
    get_bedrock_llm.cache_clear()
    get_bedrock_embedding.cache_clear()
