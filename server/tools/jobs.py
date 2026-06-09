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
from pydantic import BaseModel, Field

from server.tools.client import request


class JobSummary(BaseModel):
    """One row from Beacon's job list, kept small so the LLM context stays tight."""

    id: str
    title: str | None = None
    company: str | None = None
    location: str | None = None
    source_url: str | None = None
    status: str | None = None
    score: float | None = None
    posted_at: str | None = None


class JobDetail(JobSummary):
    """Full job record + the JD text body, when the LLM needs the JD content."""

    description: str | None = None
    requirements: list[str] = Field(default_factory=list)
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
