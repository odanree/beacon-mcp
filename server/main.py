"""FastMCP entrypoint — registers Beacon profile + job tools."""

from __future__ import annotations

import logging

from fastmcp import FastMCP

from server.config import settings
from server.tools.chatbot import ChatbotRefreshError, refresh_chatbot_rag as _refresh_chatbot_rag
from server.tools.client import BeaconAuthError, BeaconHTTPError
from server.tools.jobs import get_job, list_jobs
from server.tools.profile import (
    ProjectCreate,
    ProjectUpdate,
    add_project,
    list_projects,
    update_project,
)

logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")

mcp = FastMCP(
    "beacon-mcp",
    instructions=(
        "Operate a Beacon job-search-pipeline (https://github.com/odanree/job-search-pipeline) deployment over its REST API. "
        "Use beacon_add_project to capture a project for resume generation. "
        "Use beacon_list_projects to see what's already on the profile. "
        "Use beacon_update_project to edit fields on an existing project (e.g. backfill a missing URL). "
        "Use beacon_refresh_chatbot_rag to rebuild the ai-chatbot's RAG index from Beacon "
        "(so a project added here reaches the public chatbot on danhle.net without touching the portfolio site). "
        "Use beacon_list_jobs / beacon_get_job to inspect tracked roles. "
        "Auth is a JWT in BEACON_JWT (see README). All tools return clear "
        "errors on 401 so the user knows to refresh the token."
    ),
)


def _err(e: Exception, kind: str) -> dict:
    return {"ok": False, "error_kind": kind, "error": str(e)}


@mcp.tool()
async def beacon_add_project(
    name: str,
    description: str | None = None,
    url: str | None = None,
    tech_stack: list[str] | None = None,
    outcome: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """Add a project to the Beacon profile. Used by the resume generator.

    Args:
        name:        Short project name shown on resume bullets.
        description: 2-4 sentence project description. Surfaced verbatim in bullets.
        url:         Live demo or GitHub URL — anything clickable.
        tech_stack:  List of tools / frameworks / models. Resume generator
                     uses this to match against JD keywords.
        outcome:     Concrete result paragraph (metrics, lessons, scope).
        start_date:  YYYY-MM-DD.
        end_date:    YYYY-MM-DD or leave blank for ongoing work.
    """
    try:
        result = await add_project(ProjectCreate(
            name=name,
            description=description,
            url=url,
            tech_stack=tech_stack or [],
            outcome=outcome,
            start_date=start_date,
            end_date=end_date,
        ))
        return {"ok": True, "project": result.model_dump()}
    except BeaconAuthError as e:
        return _err(e, "auth")
    except BeaconHTTPError as e:
        return _err(e, "http")


@mcp.tool()
async def beacon_list_projects() -> dict:
    """List every project currently on your Beacon profile."""
    try:
        rows = await list_projects()
        return {"ok": True, "count": len(rows), "projects": [r.model_dump() for r in rows]}
    except BeaconAuthError as e:
        return _err(e, "auth")
    except BeaconHTTPError as e:
        return _err(e, "http")


@mcp.tool()
async def beacon_update_project(
    project_id: str,
    name: str | None = None,
    description: str | None = None,
    url: str | None = None,
    tech_stack: list[str] | None = None,
    outcome: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """Update fields on an existing Beacon project. Only supplied fields are changed.

    Args:
        project_id:  UUID of the project (from beacon_list_projects).
        name:        New project name, or omit to leave unchanged.
        description: New description, or omit to leave unchanged.
        url:         New live demo or GitHub URL, or omit to leave unchanged.
        tech_stack:  Replacement tech stack list, or omit to leave unchanged.
        outcome:     New outcome paragraph, or omit to leave unchanged.
        start_date:  YYYY-MM-DD, or omit to leave unchanged.
        end_date:    YYYY-MM-DD, or omit to leave unchanged.
    """
    # Only pass kwargs the caller actually supplied — otherwise Pydantic marks
    # every None as "set" and exclude_unset can't strip them, so the PATCH
    # body would clear unrelated fields (and hit NOT NULL on `name`).
    supplied = {
        k: v
        for k, v in {
            "name": name,
            "description": description,
            "url": url,
            "tech_stack": tech_stack,
            "outcome": outcome,
            "start_date": start_date,
            "end_date": end_date,
        }.items()
        if v is not None
    }
    try:
        result = await update_project(project_id, ProjectUpdate(**supplied))
        return {"ok": True, "project": result.model_dump()}
    except BeaconAuthError as e:
        return _err(e, "auth")
    except BeaconHTTPError as e:
        return _err(e, "http")


@mcp.tool()
async def beacon_list_jobs(status: str | None = None, limit: int = 25) -> dict:
    """List tracked jobs in the Beacon pipeline.

    Args:
        status: Pipeline stage filter (e.g. 'new', 'screen', 'interview', 'offer').
        limit:  1–100. Default 25.
    """
    try:
        rows = await list_jobs(status=status, limit=limit)
        return {"ok": True, "count": len(rows), "jobs": [r.model_dump() for r in rows]}
    except BeaconAuthError as e:
        return _err(e, "auth")
    except BeaconHTTPError as e:
        return _err(e, "http")


@mcp.tool()
async def beacon_refresh_chatbot_rag(commit_message: str | None = None) -> dict:
    """Rebuild the ai-chatbot's RAG index from Beacon and push to origin.

    Sources projects + experiences directly from Beacon (so a project added
    to Beacon that isn't on the portfolio site still lands in the chatbot).
    Runs the chatbot's `npm run build:knowledge` script with Beacon creds
    injected, then commits data/knowledge.json if it changed and pushes to
    trigger a Vercel deploy.

    Requires two env vars beyond the usual Beacon config:
      - AI_CHATBOT_PATH: path to a local ai-chatbot git checkout
      - OPENAI_API_KEY:  used by the build script for embeddings

    Args:
        commit_message: Optional commit-message override. If omitted, a
                        standard 'chore(rag): refresh knowledge.json from
                        Beacon' commit is created.

    Returns a status dict with keys `ok`, `changed`, and either
    `commit_sha` + `deploy_hint` (when a commit was pushed) or `message`
    (when the index was byte-identical to HEAD and no commit was needed).
    """
    try:
        return await _refresh_chatbot_rag(commit_message=commit_message)
    except ChatbotRefreshError as e:
        return {"ok": False, "error_kind": e.kind, "error": str(e), "detail": e.detail}


@mcp.tool()
async def beacon_get_job(job_id: str) -> dict:
    """Fetch the full record + JD body for one tracked job."""
    try:
        detail = await get_job(job_id)
        return {"ok": True, "job": detail.model_dump()}
    except BeaconAuthError as e:
        return _err(e, "auth")
    except BeaconHTTPError as e:
        return _err(e, "http")


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
