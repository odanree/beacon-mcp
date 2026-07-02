"""
Job pipeline endpoints — read-only views over the user's tracked roles.

These let Claude Code answer questions like:
  - "what jobs do I have in the 'screen' stage?"
  - "summarize the top-5 highest-scored JDs from the last 2 weeks"
  - "show the JD for job <id>"
without leaving the chat.
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel

from server.tools.client import request


class Score(BaseModel):
    """Nested score envelope on a Beacon job row.

    Mirrors the upstream `ScoreSummary` in job-search-pipeline
    (api/schemas/jobs.py). All fields are optional because jobs
    early in the pipeline haven't been scored yet, and legacy rows
    predate the `scored_with_rag` flag.
    """

    preference_score: float | None = None
    fit_score: float | None = None
    composite_score: float | None = None
    similarity_score: float | None = None
    scored_with_rag: bool | None = None


class JobSummary(BaseModel):
    """One row from Beacon's job list, kept small so the LLM context stays tight.

    Field names track the upstream API — `url` (not `source_url`),
    `application_status` (not `status`), and `score` as a nested
    object (not a flat float). Previous drift here made
    `beacon_get_job` blow up any time a job carried a score.
    """

    id: str
    title: str | None = None
    company: str | None = None
    location: str | None = None
    url: str | None = None
    application_status: str | None = None
    pipeline_phase: int | None = None
    score: Score | None = None
    posted_at: str | None = None


class JobDetail(JobSummary):
    """Full job record + the JD text body, when the LLM needs the JD content.

    Upstream exposes both `description_raw` (as scraped) and
    `description_clean` (post-processed for LLM consumption).
    Prefer `description_clean` when reading; fall back to `_raw`.
    """

    description_raw: str | None = None
    description_clean: str | None = None
    notes: str | None = None


async def list_jobs(
    status: str | None = None,
    limit: int = 25,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[JobSummary]:
    """List tracked jobs, optionally filtered by pipeline status."""
    params: dict[str, str] = {"limit": str(max(1, min(limit, 100)))}
    if status:
        params["status"] = status
    out = await request("GET", "/api/jobs", params=params, client=client)
    # Beacon endpoints vary in envelope shape; accept either {items:[]} or [].
    rows = out if isinstance(out, list) else out.get("items", [])
    return [JobSummary.model_validate(r) for r in rows]


async def get_job(job_id: str, *, client: httpx.AsyncClient | None = None) -> JobDetail:
    """Pull the full record + JD text for one job."""
    out = await request("GET", f"/api/jobs/{job_id}", client=client)
    return JobDetail.model_validate(out)
