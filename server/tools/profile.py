"""
Profile + project + experience endpoints.

Replaces the curl + heredoc + cookie-grab dance with a single typed call.
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel, Field

from server.tools.client import request


class ProjectCreate(BaseModel):
    """Mirrors Beacon's POST /api/profile/projects body.

    `talking_points` is the interview-prep prose surface: narrative framing,
    cross-links, meta-loops. It is deliberately withheld from resume
    generation (read-path separation) and only feeds interview-prep + the
    ai-chatbot RAG.
    """

    name: str
    description: str | None = None
    url: str | None = None
    tech_stack: list[str] = Field(default_factory=list)
    outcome: str | None = None
    talking_points: str | None = None
    start_date: str | None = None     # YYYY-MM-DD
    end_date: str | None = None


class ProjectUpdate(BaseModel):
    """Mirrors Beacon's PATCH /api/profile/projects/{id} body. All fields optional."""

    name: str | None = None
    description: str | None = None
    url: str | None = None
    tech_stack: list[str] | None = None
    outcome: str | None = None
    talking_points: str | None = None
    start_date: str | None = None
    end_date: str | None = None


class ProjectSummary(BaseModel):
    """Beacon's project envelope as the tool surfaces it back to the LLM."""

    id: str
    name: str
    url: str | None = None
    tech_stack: list[str] = Field(default_factory=list)
    description: str | None = None
    outcome: str | None = None
    talking_points: str | None = None
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


async def update_project(
    project_id: str,
    p: ProjectUpdate,
    *,
    client: httpx.AsyncClient | None = None,
) -> ProjectSummary:
    """PATCH an existing project. Only supplied fields are touched."""
    payload = p.model_dump(exclude_unset=True)
    out = await request(
        "PATCH", f"/api/profile/projects/{project_id}", json=payload, client=client
    )
    return ProjectSummary.model_validate(out)


# ---------------------------------------------------------------------------
# Experiences
# ---------------------------------------------------------------------------


class ExperienceCreate(BaseModel):
    """Mirrors Beacon's POST /api/profile/experiences body.

    `talking_points` is the interview-only prose surface (STAR stories,
    mentorship arcs, behavioral-question context). Beacon deliberately
    withholds it from resume generation; it feeds interview prep and the
    ai-chatbot RAG.
    """

    company: str
    title: str
    start_date: str  # YYYY-MM-DD
    end_date: str | None = None
    is_current: bool = False
    location: str | None = None
    description: str | None = None
    tech_stack: list[str] = Field(default_factory=list)
    impact_metrics: str | None = None
    talking_points: str | None = None


class ExperienceUpdate(BaseModel):
    """Mirrors Beacon's PUT /api/profile/experiences/{id} body. All fields
    optional; Beacon's endpoint applies `exclude_unset=True` so unset fields
    are preserved.
    """

    company: str | None = None
    title: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    is_current: bool | None = None
    location: str | None = None
    description: str | None = None
    tech_stack: list[str] | None = None
    impact_metrics: str | None = None
    talking_points: str | None = None


class ExperienceSummary(BaseModel):
    """Beacon's experience envelope as the tool surfaces it back to the LLM."""

    id: str
    company: str
    title: str
    start_date: str
    end_date: str | None = None
    is_current: bool = False
    location: str | None = None
    description: str | None = None
    tech_stack: list[str] = Field(default_factory=list)
    impact_metrics: str | None = None
    talking_points: str | None = None


async def add_experience(
    e: ExperienceCreate, *, client: httpx.AsyncClient | None = None
) -> ExperienceSummary:
    """POST a new work-history entry to your Beacon profile."""
    payload = e.model_dump(exclude_none=True)
    out = await request("POST", "/api/profile/experiences", json=payload, client=client)
    return ExperienceSummary.model_validate(out)


async def list_experiences(
    *, client: httpx.AsyncClient | None = None
) -> list[ExperienceSummary]:
    """List every experience currently on your Beacon profile."""
    out = await request("GET", "/api/profile/experiences", client=client)
    if not isinstance(out, list):
        return []
    return [ExperienceSummary.model_validate(x) for x in out]


async def update_experience(
    exp_id: str,
    e: ExperienceUpdate,
    *,
    client: httpx.AsyncClient | None = None,
) -> ExperienceSummary:
    """PUT — under exclude_unset semantics — an existing experience row.

    Only supplied fields are touched; the Beacon API applies `exclude_unset=True`
    on its side so unset fields are preserved.
    """
    payload = e.model_dump(exclude_unset=True)
    out = await request(
        "PUT", f"/api/profile/experiences/{exp_id}", json=payload, client=client
    )
    return ExperienceSummary.model_validate(out)
