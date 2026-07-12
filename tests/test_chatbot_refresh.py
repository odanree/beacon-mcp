"""Tests for `refresh_chatbot_rag` — HTTP fully mocked, no network.

Focus of this suite: the webhook mode added in ADR-021 phase 2. Local
mode is a subprocess pipeline covered by manual + integration testing
(shells out to bun + git, hard to unit-test cheaply).
"""

from __future__ import annotations

import httpx
import pytest
import respx

import server.config as cfg_module
from server.tools.chatbot import ChatbotRefreshError, refresh_chatbot_rag


@pytest.fixture(autouse=True)
def _mock_settings(monkeypatch):
    """Reset every setting the refresh path might touch to a known state."""
    monkeypatch.setattr(cfg_module.settings, "vercel_deploy_hook_url", "")
    monkeypatch.setattr(cfg_module.settings, "ai_chatbot_path", "")
    monkeypatch.setattr(cfg_module.settings, "openai_api_key", "")


@pytest.mark.asyncio
async def test_unknown_mode_raises_config_error():
    with pytest.raises(ChatbotRefreshError) as exc_info:
        await refresh_chatbot_rag(mode="lambda")  # type: ignore[arg-type]
    assert exc_info.value.kind == "config"
    assert "unknown mode" in str(exc_info.value)


@pytest.mark.asyncio
async def test_webhook_without_url_raises_config_error(monkeypatch):
    with pytest.raises(ChatbotRefreshError) as exc_info:
        await refresh_chatbot_rag(mode="webhook")
    assert exc_info.value.kind == "config"
    assert "VERCEL_DEPLOY_HOOK_URL" in str(exc_info.value)


@respx.mock
@pytest.mark.asyncio
async def test_webhook_success_returns_deploy_job(monkeypatch):
    monkeypatch.setattr(
        cfg_module.settings,
        "vercel_deploy_hook_url",
        "https://api.vercel.com/v1/integrations/deploy/prj_test/token123",
    )
    route = respx.post(
        "https://api.vercel.com/v1/integrations/deploy/prj_test/token123"
    ).mock(
        return_value=httpx.Response(
            201,
            json={
                "job": {
                    "id": "cLYlLkVU5NNiRjZfnGGmvNHOJmz2",
                    "state": "PENDING",
                    "createdAt": 1723456789,
                }
            },
        )
    )

    out = await refresh_chatbot_rag(mode="webhook")

    assert route.called
    assert out["ok"] is True
    assert out["mode"] == "webhook"
    assert out["deploy_hook_response"]["job"]["id"] == "cLYlLkVU5NNiRjZfnGGmvNHOJmz2"
    assert "Vercel" in out["deploy_hint"]


@respx.mock
@pytest.mark.asyncio
async def test_webhook_non_json_response_still_succeeds(monkeypatch):
    """Some Vercel Deploy Hook responses can be empty or non-JSON — we shouldn't crash."""
    monkeypatch.setattr(
        cfg_module.settings,
        "vercel_deploy_hook_url",
        "https://api.vercel.com/v1/integrations/deploy/prj_test/token123",
    )
    respx.post(
        "https://api.vercel.com/v1/integrations/deploy/prj_test/token123"
    ).mock(return_value=httpx.Response(200, text="ok"))

    out = await refresh_chatbot_rag(mode="webhook")

    assert out["ok"] is True
    assert "raw" in out["deploy_hook_response"]


@respx.mock
@pytest.mark.asyncio
async def test_webhook_500_raises_webhook_error(monkeypatch):
    monkeypatch.setattr(
        cfg_module.settings,
        "vercel_deploy_hook_url",
        "https://api.vercel.com/v1/integrations/deploy/prj_test/token123",
    )
    respx.post(
        "https://api.vercel.com/v1/integrations/deploy/prj_test/token123"
    ).mock(return_value=httpx.Response(503, text="Deploy queue temporarily unavailable"))

    with pytest.raises(ChatbotRefreshError) as exc_info:
        await refresh_chatbot_rag(mode="webhook")

    assert exc_info.value.kind == "webhook"
    assert "503" in str(exc_info.value)
    assert "Deploy queue" in exc_info.value.detail


@respx.mock
@pytest.mark.asyncio
async def test_webhook_transport_error_raises_webhook_error(monkeypatch):
    monkeypatch.setattr(
        cfg_module.settings,
        "vercel_deploy_hook_url",
        "https://api.vercel.com/v1/integrations/deploy/prj_test/token123",
    )
    respx.post(
        "https://api.vercel.com/v1/integrations/deploy/prj_test/token123"
    ).mock(side_effect=httpx.ConnectError("Connection refused"))

    with pytest.raises(ChatbotRefreshError) as exc_info:
        await refresh_chatbot_rag(mode="webhook")

    assert exc_info.value.kind == "webhook"
    assert "transport" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_local_mode_without_config_raises_config_error(monkeypatch):
    """Local mode's config checks still fire (existing behavior — regression guard)."""
    with pytest.raises(ChatbotRefreshError) as exc_info:
        await refresh_chatbot_rag(mode="local")
    assert exc_info.value.kind == "config"
    assert "AI_CHATBOT_PATH" in str(exc_info.value)


@pytest.mark.asyncio
async def test_default_mode_is_local(monkeypatch):
    """Backward compatibility — omitting `mode` preserves the existing local path."""
    with pytest.raises(ChatbotRefreshError) as exc_info:
        await refresh_chatbot_rag()
    # If we defaulted to webhook we'd complain about VERCEL_DEPLOY_HOOK_URL first;
    # complaining about AI_CHATBOT_PATH means we took the local branch.
    assert "AI_CHATBOT_PATH" in str(exc_info.value)
