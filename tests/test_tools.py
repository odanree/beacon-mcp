"""Tests for the Beacon API wrappers — HTTP fully mocked, no network."""

from __future__ import annotations

import httpx
import pytest
import respx

import server.config as cfg_module
from server.tools.client import BeaconAuthError, BeaconHTTPError, request
from server.tools.jobs import get_job, list_jobs
from server.tools.profile import (
    ProjectCreate,
    ProjectUpdate,
    add_project,
    list_projects,
    update_project,
)


@pytest.fixture(autouse=True)
def _set_jwt(monkeypatch):
    """Every test runs as if a JWT is configured."""
    monkeypatch.setattr(cfg_module.settings, "beacon_jwt", "test-jwt-token")
    monkeypatch.setattr(cfg_module.settings, "beacon_api_url", "https://beacon.test")


@respx.mock
@pytest.mark.asyncio
async def test_add_project_posts_and_returns_envelope():
    route = respx.post("https://beacon.test/api/profile/projects").mock(
        return_value=httpx.Response(201, json={
            "id": "abc-123", "name": "infra-mcp", "url": "https://github.com/x/infra-mcp",
            "tech_stack": ["MCP", "FastMCP"], "description": "d", "outcome": "o",
            "start_date": "2026-06-08", "end_date": None,
        })
    )
    p = ProjectCreate(name="infra-mcp", url="https://github.com/x/infra-mcp",
                      tech_stack=["MCP", "FastMCP"], description="d", outcome="o",
                      start_date="2026-06-08")
    out = await add_project(p)
    assert out.id == "abc-123"
    assert out.tech_stack == ["MCP", "FastMCP"]
    body = route.calls[0].request.content.decode()
    assert "infra-mcp" in body
    assert route.calls[0].request.headers["Authorization"] == "Bearer test-jwt-token"


@respx.mock
@pytest.mark.asyncio
async def test_update_project_patches_only_supplied_fields():
    route = respx.patch("https://beacon.test/api/profile/projects/abc-123").mock(
        return_value=httpx.Response(200, json={
            "id": "abc-123", "name": "parking-enforcement-detector",
            "url": "https://github.com/odanree/parking-enforcement-detector",
            "tech_stack": [], "description": None, "outcome": None,
            "start_date": None, "end_date": None,
        })
    )
    out = await update_project(
        "abc-123",
        ProjectUpdate(url="https://github.com/odanree/parking-enforcement-detector"),
    )
    assert out.url == "https://github.com/odanree/parking-enforcement-detector"
    body = route.calls[0].request.content.decode()
    assert "url" in body
    assert "name" not in body  # exclude_unset=True dropped the other fields
    assert route.calls[0].request.headers["Authorization"] == "Bearer test-jwt-token"


@respx.mock
@pytest.mark.asyncio
async def test_beacon_update_project_tool_omits_none_defaults():
    """Regression: tool was sending {name: null, ...} which 500'd against the
    NOT NULL `name` column. Now it should drop None defaults before the PATCH."""
    from server.main import beacon_update_project

    route = respx.patch("https://beacon.test/api/profile/projects/abc-123").mock(
        return_value=httpx.Response(200, json={
            "id": "abc-123", "name": "x",
            "url": "https://github.com/x/y",
            "tech_stack": [], "description": None, "outcome": None,
            "start_date": None, "end_date": None,
        })
    )
    out = await beacon_update_project(
        project_id="abc-123", url="https://github.com/x/y"
    )
    assert out["ok"] is True
    body = route.calls[0].request.content.decode()
    assert '"url"' in body
    for absent in ('"name"', '"description"', '"tech_stack"', '"outcome"', '"start_date"', '"end_date"'):
        assert absent not in body, f"PATCH body should not include {absent}: {body}"


@respx.mock
@pytest.mark.asyncio
async def test_list_projects_returns_empty_when_envelope_is_not_a_list():
    respx.get("https://beacon.test/api/profile/projects").mock(
        return_value=httpx.Response(200, json={"unexpected": "shape"}),
    )
    rows = await list_projects()
    assert rows == []


@respx.mock
@pytest.mark.asyncio
async def test_request_raises_auth_error_on_401():
    respx.get("https://beacon.test/api/profile/projects").mock(
        return_value=httpx.Response(401, json={"detail": "expired"})
    )
    with pytest.raises(BeaconAuthError) as e:
        await request("GET", "/api/profile/projects")
    assert "expired" in str(e.value).lower() or "401" in str(e.value)


@respx.mock
@pytest.mark.asyncio
async def test_request_raises_http_error_on_5xx():
    respx.get("https://beacon.test/api/jobs").mock(
        return_value=httpx.Response(503, text="upstream down")
    )
    with pytest.raises(BeaconHTTPError) as e:
        await request("GET", "/api/jobs")
    assert "503" in str(e.value)


@pytest.mark.asyncio
async def test_request_requires_jwt(monkeypatch):
    monkeypatch.setattr(cfg_module.settings, "beacon_jwt", "")
    with pytest.raises(BeaconAuthError):
        await request("GET", "/api/profile/projects")


@respx.mock
@pytest.mark.asyncio
async def test_list_jobs_passes_status_and_limit_params():
    route = respx.get("https://beacon.test/api/jobs").mock(
        return_value=httpx.Response(200, json=[{
            "id": "job-1", "title": "Sr ML Engineer", "company": "Foo",
            "application_status": "screen",
            "score": {
                "preference_score": 8.4,
                "fit_score": 9.1,
                "composite_score": 8.82,
                "scored_with_rag": True,
            },
        }]),
    )
    rows = await list_jobs(status="screen", limit=10)
    assert len(rows) == 1
    assert rows[0].application_status == "screen"
    assert rows[0].score is not None
    assert rows[0].score.preference_score == 8.4
    assert rows[0].score.composite_score == 8.82
    params = dict(route.calls[0].request.url.params)
    assert params == {"limit": "10", "status": "screen"}


@respx.mock
@pytest.mark.asyncio
async def test_list_jobs_clamps_limit():
    captured = {}

    def _capture(request):
        captured.update(dict(request.url.params))
        return httpx.Response(200, json=[])

    respx.get("https://beacon.test/api/jobs").mock(side_effect=_capture)
    await list_jobs(limit=999999)
    assert captured["limit"] == "100"


@respx.mock
@pytest.mark.asyncio
async def test_list_jobs_handles_envelope_with_items_key():
    """Beacon's job list can come back as {items: [...]} on some endpoints."""
    respx.get("https://beacon.test/api/jobs").mock(
        return_value=httpx.Response(200, json={"items": [
            {"id": "j-1", "title": "A"},
            {"id": "j-2", "title": "B"},
        ]}),
    )
    rows = await list_jobs()
    assert {r.id for r in rows} == {"j-1", "j-2"}


@respx.mock
@pytest.mark.asyncio
async def test_get_job_returns_full_detail():
    respx.get("https://beacon.test/api/jobs/abc-1").mock(
        return_value=httpx.Response(200, json={
            "id": "abc-1",
            "title": "Sr AI",
            "description_raw": "raw JD text",
            "description_clean": "cleaned JD text",
            "notes": "recruiter reached out day 1",
            "score": {
                "preference_score": 3.5,
                "fit_score": 8.0,
                "composite_score": 6.2,
                "scored_with_rag": False,
            },
        }),
    )
    out = await get_job("abc-1")
    assert out.id == "abc-1"
    assert out.description_clean == "cleaned JD text"
    assert out.description_raw == "raw JD text"
    assert out.notes == "recruiter reached out day 1"
    assert out.score is not None
    assert out.score.preference_score == 3.5
    assert out.score.scored_with_rag is False


@respx.mock
@pytest.mark.asyncio
async def test_get_job_tolerates_unscored_row():
    """Early-pipeline jobs have no score object — must not blow up."""
    respx.get("https://beacon.test/api/jobs/pending-1").mock(
        return_value=httpx.Response(200, json={
            "id": "pending-1",
            "title": "Fresh scrape",
            "score": None,
        }),
    )
    out = await get_job("pending-1")
    assert out.id == "pending-1"
    assert out.score is None
