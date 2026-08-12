"""FastMCP entrypoint — registers Beacon profile + job tools."""

from __future__ import annotations

import logging

from fastmcp import FastMCP

from server.config import settings
from server.tools.chatbot import ChatbotRefreshError
from server.tools.chatbot import refresh_chatbot_rag as _refresh_chatbot_rag
from server.tools.client import BeaconAuthError, BeaconHTTPError
from server.tools.jobs import get_job, list_jobs
from server.tools.profile import (
    ExperienceCreate,
    ExperienceUpdate,
    ProjectCreate,
    ProjectUpdate,
    add_experience,
    add_project,
    list_experiences,
    list_projects,
    update_experience,
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
        "Use beacon_add_experience / beacon_list_experiences / beacon_update_experience for work-history rows. "
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
    talking_points: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """Add a project to the Beacon profile. Used by the resume generator.

    Args:
        name:           Short project name shown on resume bullets.
        description:    2-4 sentence project description. Surfaced verbatim in bullets.
        url:            Live demo or GitHub URL — anything clickable.
        tech_stack:     List of tools / frameworks / models. Resume generator
                        uses this to match against JD keywords.
        outcome:        Concrete result paragraph (metrics, lessons, scope).
        talking_points: Interview-only prose — narrative framing, cross-links,
                        meta-loops. Read by interview prep and the ai-chatbot;
                        deliberately withheld from resume generation.
        start_date:     YYYY-MM-DD.
        end_date:       YYYY-MM-DD or leave blank for ongoing work.
    """
    try:
        result = await add_project(ProjectCreate(
            name=name,
            description=description,
            url=url,
            tech_stack=tech_stack or [],
            outcome=outcome,
            talking_points=talking_points,
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
    talking_points: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """Update fields on an existing Beacon project. Only supplied fields are changed.

    Args:
        project_id:     UUID of the project (from beacon_list_projects).
        name:           New project name, or omit to leave unchanged.
        description:    New description, or omit to leave unchanged.
        url:            New live demo or GitHub URL, or omit to leave unchanged.
        tech_stack:     Replacement tech stack list, or omit to leave unchanged.
        outcome:        New outcome paragraph, or omit to leave unchanged.
        talking_points: New interview-only prose, or omit to leave unchanged.
                        Never rendered on a resume; used by interview prep
                        and the ai-chatbot RAG.
        start_date:     YYYY-MM-DD, or omit to leave unchanged.
        end_date:       YYYY-MM-DD, or omit to leave unchanged.
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
            "talking_points": talking_points,
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
async def beacon_add_experience(
    company: str,
    title: str,
    start_date: str,
    end_date: str | None = None,
    is_current: bool = False,
    location: str | None = None,
    description: str | None = None,
    tech_stack: list[str] | None = None,
    impact_metrics: str | None = None,
    talking_points: str | None = None,
) -> dict:
    """Add a work-history entry to the Beacon profile.

    Args:
        company:        Employer name shown on resume.
        title:          Role title shown on resume.
        start_date:     YYYY-MM-DD.
        end_date:       YYYY-MM-DD, or omit for ongoing / current roles.
        is_current:     Set True when the role is ongoing; end_date is ignored.
        location:       City / metro / "Remote".
        description:    Bullet-formatted responsibilities + achievements. Surfaces
                        on resume via phase 4.
        tech_stack:     List of tools / frameworks. Resume generator matches
                        against JD keywords.
        impact_metrics: Concrete outcomes (percentages, dollars, counts).
                        Surfaces on resume as an italicized impact line.
        talking_points: Interview-only prose — STAR stories, mentorship arcs,
                        behavioral-question context. Read by interview prep
                        and the ai-chatbot; deliberately withheld from resume.
    """
    try:
        result = await add_experience(
            ExperienceCreate(
                company=company,
                title=title,
                start_date=start_date,
                end_date=end_date,
                is_current=is_current,
                location=location,
                description=description,
                tech_stack=tech_stack or [],
                impact_metrics=impact_metrics,
                talking_points=talking_points,
            )
        )
        return {"ok": True, "experience": result.model_dump()}
    except BeaconAuthError as e:
        return _err(e, "auth")
    except BeaconHTTPError as e:
        return _err(e, "http")


@mcp.tool()
async def beacon_list_experiences() -> dict:
    """List every work-history entry currently on your Beacon profile."""
    try:
        rows = await list_experiences()
        return {
            "ok": True,
            "count": len(rows),
            "experiences": [r.model_dump() for r in rows],
        }
    except BeaconAuthError as e:
        return _err(e, "auth")
    except BeaconHTTPError as e:
        return _err(e, "http")


@mcp.tool()
async def beacon_update_experience(
    experience_id: str,
    company: str | None = None,
    title: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    is_current: bool | None = None,
    location: str | None = None,
    description: str | None = None,
    tech_stack: list[str] | None = None,
    impact_metrics: str | None = None,
    talking_points: str | None = None,
) -> dict:
    """Update fields on an existing Beacon experience row. Only supplied fields
    are changed — Beacon's endpoint applies exclude_unset semantics.

    Args:
        experience_id:  UUID of the experience (from beacon_list_experiences).
        talking_points: New interview-only prose, or omit to leave unchanged.
                        Never rendered on a resume; used by interview prep
                        and the ai-chatbot RAG.
        (all others):   Omit to leave unchanged.
    """
    # Only pass kwargs the caller actually supplied — otherwise Pydantic marks
    # every None as "set" and exclude_unset can't strip them, so the PUT body
    # would clear unrelated fields.
    supplied = {
        k: v
        for k, v in {
            "company": company,
            "title": title,
            "start_date": start_date,
            "end_date": end_date,
            "is_current": is_current,
            "location": location,
            "description": description,
            "tech_stack": tech_stack,
            "impact_metrics": impact_metrics,
            "talking_points": talking_points,
        }.items()
        if v is not None
    }
    try:
        result = await update_experience(experience_id, ExperienceUpdate(**supplied))
        return {"ok": True, "experience": result.model_dump()}
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
async def beacon_refresh_chatbot_rag(
    mode: str = "local",
    commit_message: str | None = None,
) -> dict:
    """Rebuild the ai-chatbot's RAG index from Beacon.

    Sources projects + experiences directly from Beacon (so a project added
    to Beacon that isn't on the portfolio site still lands in the chatbot).

    Two modes:

      - "local" (default): shells out to `bun run src/knowledge/build.ts`
        in the local ai-chatbot checkout (AI_CHATBOT_PATH), commits +
        pushes if data/knowledge.json changed. Vercel auto-deploys on the
        push. Requires local Bun runtime + git-push credentials.
        Env: AI_CHATBOT_PATH + OPENAI_API_KEY.

      - "webhook" (ADR-021): POSTs to VERCEL_DEPLOY_HOOK_URL. Vercel
        rebuilds via its own `vercel-build` script (which regenerates
        knowledge.json from Beacon). No local checkout, no local Bun,
        no local push credentials. Fire-and-forget: returns immediately
        with the Vercel deploy job ID; the deploy itself takes ~30–60s.
        Env: VERCEL_DEPLOY_HOOK_URL.

    Args:
        mode: "local" (default) or "webhook". See above.
        commit_message: Optional commit-message override (local mode only).

    Returns a status dict. Shape varies by mode:
        local:   {ok, changed, commit_sha?, commit_message?, deploy_hint?, message?}
        webhook: {ok, mode, deploy_hook_response, deploy_hint}
    """
    if mode not in ("local", "webhook"):
        return {
            "ok": False,
            "error_kind": "config",
            "error": f"unknown mode {mode!r} — expected 'local' or 'webhook'.",
        }
    try:
        return await _refresh_chatbot_rag(mode=mode, commit_message=commit_message)  # type: ignore[arg-type]
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
