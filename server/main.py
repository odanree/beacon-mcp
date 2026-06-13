"""FastMCP entrypoint — registers Beacon profile + job tools."""

from __future__ import annotations

import logging

from fastmcp import FastMCP

from server.config import settings
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
    try:
        result = await update_project(
            project_id,
            ProjectUpdate(
                name=name,
                description=description,
                url=url,
                tech_stack=tech_stack,
                outcome=outcome,
                start_date=start_date,
                end_date=end_date,
            ),
        )
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
