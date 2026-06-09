"""
Profile + project endpoints.

Replaces the curl + heredoc + cookie-grab dance with a single typed call.
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel, Field

from server.tools.client import request


class ProjectCreate(BaseModel):
    """Mirrors Beacon's POST /api/profile/projects body."""

    name: str
    description: str | None = None
    url: str | None = None
    tech_stack: list[str] = Field(default_factory=list)
    outcome: str | None = None
    start_date: str | None = None     # YYYY-MM-DD
    end_date: str | None = None


class ProjectSummary(BaseModel):
    """Beacon's project envelope as the tool surfaces it back to the LLM."""

    id: str
    name: str
    url: str | None = None
    tech_stack: list[str] = Field(default_factory=list)
    description: str | None = None
    outcome: str | None = None
    start_date: str | None = None
    end_date: str | None = None


async def add_project(p: ProjectCreate, *, client: httpx.AsyncClient | None = None) -> ProjectSummary:
    """POST a new project entry to your Beacon profile.

    Use this any time you want to capture a project for resume generation.
    Beacon's resume generator will pull these into bullet points for any
    matching JD.
    """
    payload = p.model_dump(exclude_none=True)
    out = await request("POST", "/api/profile/projects", json=payload, client=client)
    return ProjectSummary.model_validate(out)


async def list_projects(*, client: httpx.AsyncClient | None = None) -> list[ProjectSummary]:
    """List every project currently on your Beacon profile."""
    out = await request("GET", "/api/profile/projects", client=client)
    if not isinstance(out, list):
        return []
    return [ProjectSummary.model_validate(p) for p in out]
