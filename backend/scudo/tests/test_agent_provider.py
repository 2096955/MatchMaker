"""Focused tests for agent provider selection and Azure OpenAI shim.

Pins:
    - Provider discovery endpoint (GET /api/mapping/agent/describe)
    - Validation of unknown agent_provider
    - AzureOpenAIShim behaves correctly and falls back without reasoning_effort
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

os.environ.setdefault("STORE_BACKEND", "memory")
os.environ.setdefault("FRAME_SOURCE", "mock")
os.environ.setdefault("SCUDO_DENSE_BACKEND", "jaro_winkler")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app import app
from scudo.lambda_handler import AzureOpenAIShim, handler
from scudo.schemas import MappingResult, Band

client = app.test_client()
AUTH = {"X-Authenticated-User": "2096955@cognizant.com"}


def test_agent_describe_endpoint():
    """Verify provider discovery is returned in /api/mapping/agent/describe.

    Azure only reads as enabled once every field AzureMappingAgent's
    _require_config actually needs is set (endpoint alone is not enough —
    see test_agent_describe_endpoint_azure_requires_full_config below).
    """
    with patch.dict(
        os.environ,
        {
            "AZURE_OPENAI_ENDPOINT": "https://test-azure.com",
            "AZURE_OPENAI_API_KEY": "test-key",
            "AZURE_OPENAI_SPECIALIST_DEPLOYMENT": "gpt-4o",
        },
    ):
        r = client.get("/api/mapping/agent/describe", headers=AUTH)
        assert r.status_code == 200
        data = r.get_json()
        assert "default_provider" in data
        assert "providers" in data
        providers = data["providers"]
        assert any(p["id"] == "bedrock" and p["enabled"] is True for p in providers)
        assert any(p["id"] == "azure" and p["enabled"] is True for p in providers)


def test_agent_describe_endpoint_azure_requires_full_config():
    """An endpoint set alone must NOT read as enabled — that would offer a
    runtime in the UI that always fails at first run."""
    with patch.dict(os.environ, {"AZURE_OPENAI_ENDPOINT": "https://test-azure.com"}):
        os.environ.pop("AZURE_OPENAI_API_KEY", None)
        os.environ.pop("AZURE_OPENAI_SPECIALIST_DEPLOYMENT", None)
        r = client.get("/api/mapping/agent/describe", headers=AUTH)
        providers = r.get_json()["providers"]
        assert any(p["id"] == "azure" and p["enabled"] is False for p in providers)


def test_agent_describe_endpoint_disabled_azure():
    """Verify azure is disabled when no endpoint is configured."""
    with patch.dict(os.environ, {}, clear=True):
        if "AZURE_OPENAI_ENDPOINT" in os.environ:
            del os.environ["AZURE_OPENAI_ENDPOINT"]
        r = client.get("/api/mapping/agent/describe", headers=AUTH)
        assert r.status_code == 200
        data = r.get_json()
        providers = data["providers"]
        assert any(p["id"] == "azure" and p["enabled"] is False for p in providers)


def test_lambda_unknown_provider_rejection():
    """Check that passing an invalid agent_provider results in a 400 rejection."""
    event = {
        "path": "/run",
        "httpMethod": "POST",
        "body": '{"vendor": "lseg", "vendor_product_ref": "LSEG-IBES-EST-001", "agent_provider": "unknown_provider"}',
    }
    resp = handler(event, None)
    assert resp["statusCode"] == 400
    body = resp["body"]
    assert "unknown agent provider" in body


@patch("openai.AzureOpenAI")
def test_azure_openai_shim_success(mock_azure_client):
    """Verify that AzureOpenAIShim correctly uses the client.beta.chat.completions.parse API."""
    # Setup mock Pydantic output
    mock_result = MappingResult(
        vendor_product_iri="mds.lseg:test-uuid",
        proposed_target_iri="jpmorgan:data:cdao:TestNode",
        rationale="Matches perfectly based on mock dense scoring",
        confidence=0.85,
        band=Band.HIGH,
        evidence=[],
    )

    # Configure Azure OpenAI mocks
    mock_choice = MagicMock()
    mock_choice.message.parsed = mock_result

    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    mock_instance = MagicMock()
    mock_instance.beta.chat.completions.parse.return_value = mock_response
    mock_azure_client.return_value = mock_instance

    # Construct shim
    shim = AzureOpenAIShim(
        system_prompt="You are a helper.",
        deployment_env_var="AZURE_OPENAI_SPECIALIST_DEPLOYMENT",
    )

    # Assert correct parameters are read and passed
    with patch.dict(
        os.environ,
        {
            "AZURE_OPENAI_ENDPOINT": "https://mock-endpoint.com",
            "AZURE_OPENAI_API_KEY": "test-key",
            "AZURE_OPENAI_SPECIALIST_DEPLOYMENT": "gpt-4o",
            "AZURE_OPENAI_REASONING_EFFORT": "medium",
        },
    ):
        res = shim("test prompt", MappingResult)

        # Test __call__ wrapper shape
        assert hasattr(res, "structured_output")
        assert res.structured_output == mock_result

        # Verify first call with reasoning_effort
        mock_instance.beta.chat.completions.parse.assert_called_with(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a helper."},
                {"role": "user", "content": "test prompt"},
            ],
            response_format=MappingResult,
            reasoning_effort="medium",
        )


@patch("openai.AzureOpenAI")
def test_azure_openai_shim_fallback(mock_azure_client):
    """Verify that AzureOpenAIShim falls back safely if reasoning_effort raises an error."""
    mock_result = MappingResult(
        vendor_product_iri="mds.lseg:test-uuid",
        proposed_target_iri="jpmorgan:data:cdao:TestNode",
        rationale="Matches perfectly based on mock dense scoring",
        confidence=0.85,
        band=Band.HIGH,
        evidence=[],
    )

    mock_choice = MagicMock()
    mock_choice.message.parsed = mock_result

    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    mock_instance = MagicMock()
    # First call fails, second call (fallback) succeeds
    mock_instance.beta.chat.completions.parse.side_effect = [
        TypeError("reasoning_effort unexpected keyword argument"),
        mock_response,
    ]
    mock_azure_client.return_value = mock_instance

    shim = AzureOpenAIShim(
        system_prompt="You are a helper.",
        deployment_env_var="AZURE_OPENAI_SPECIALIST_DEPLOYMENT",
    )

    with patch.dict(
        os.environ,
        {
            "AZURE_OPENAI_ENDPOINT": "https://mock-endpoint.com",
            "AZURE_OPENAI_API_KEY": "test-key",
            "AZURE_OPENAI_SPECIALIST_DEPLOYMENT": "gpt-4o",
        },
    ):
        res = shim("test prompt", MappingResult)
        assert res.structured_output == mock_result

        # Assert both attempts were called correctly
        assert mock_instance.beta.chat.completions.parse.call_count == 2


@patch("openai.AzureOpenAI")
def test_azure_openai_shim_missing_deployment_fails_clearly(mock_azure_client):
    """endpoint + api_key present but the deployment env var unset must raise
    a clear ValueError naming that var — never call Azure with model=None."""
    shim = AzureOpenAIShim(
        system_prompt="You are a helper.",
        deployment_env_var="AZURE_OPENAI_VERIFIER_DEPLOYMENT",
    )

    with patch.dict(
        os.environ,
        {
            "AZURE_OPENAI_ENDPOINT": "https://mock-endpoint.com",
            "AZURE_OPENAI_API_KEY": "test-key",
        },
    ):
        os.environ.pop("AZURE_OPENAI_VERIFIER_DEPLOYMENT", None)
        try:
            shim("test prompt", MappingResult)
            raise AssertionError("expected ValueError for missing deployment")
        except ValueError as e:
            assert "AZURE_OPENAI_VERIFIER_DEPLOYMENT" in str(e)

    mock_azure_client.return_value.beta.chat.completions.parse.assert_not_called()
