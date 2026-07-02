"""Contract test — verify beacon-mcp's pydantic response models still
match what upstream job-search-pipeline promises in its OpenAPI schema.

Prevents drift like beacon-mcp#3 (JobDetail.score was declared `float`
while upstream returned a nested `ScoreSummary` object — silently broken
until the first scored job).

## Source of truth

`https://raw.githubusercontent.com/odanree/job-search-pipeline/master/api/openapi.json`

is regenerated on every JSP push (job-search-pipeline#191 wires the
drift check into CI). Override via `$JSP_OPENAPI_URL` — accepts either
an http(s) URL or a local file path (handy for testing against a JSP
feature branch or a local dump).

## Behavior when upstream is unreachable

If the schema URL 404s or the network is down, tests skip with a clear
message rather than failing. This keeps the CI signal honest — we don't
know the answer, so we say so — and the scheduled nightly run picks up
the schema as soon as JSP publishes it.

## What this catches

1. **Renamed / removed fields** — every field beacon-mcp declares must
   exist as a property on the upstream component. Caught the
   `source_url` → `url` and `status` → `application_status` drift
   retroactively.
2. **Scalar-where-upstream-nests** — if upstream declares a field via
   `$ref` (a nested object), the beacon-mcp field's annotation must
   contain a `BaseModel` somewhere. Caught the `score: float` vs
   `score: ScoreSummary` drift retroactively.

Type-narrowing drift (e.g. upstream int → beacon-mcp bool) is out of
scope for now — the two classes above cover the drift shapes we've
actually seen.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, get_args

import httpx
import pytest
from pydantic import BaseModel

from server.tools.jobs import JobDetail, JobSummary, Score
from server.tools.profile import ProjectSummary

_DEFAULT_OPENAPI_URL = (
    "https://raw.githubusercontent.com/"
    "odanree/job-search-pipeline/master/api/openapi.json"
)


def _load_openapi() -> dict | None:
    source = os.environ.get("JSP_OPENAPI_URL", _DEFAULT_OPENAPI_URL)
    if source.startswith(("http://", "https://")):
        try:
            r = httpx.get(source, timeout=10.0)
        except httpx.RequestError:
            return None
        if r.status_code != 200:
            return None
        return r.json()
    # Treat as local path.
    p = Path(source)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def upstream_openapi() -> dict:
    schema = _load_openapi()
    if schema is None:
        pytest.skip(
            "Upstream job-search-pipeline openapi.json is not reachable. "
            "Once JSP publishes api/openapi.json to master, contract "
            "tests will run automatically on the next CI trigger "
            "(push, PR, or nightly cron)."
        )
    return schema


def _resolve_ref(ref: str, openapi: dict) -> dict:
    assert ref.startswith("#/components/schemas/"), ref
    name = ref.split("/")[-1]
    return openapi["components"]["schemas"][name]


def _properties(schema: dict) -> dict[str, dict]:
    return schema.get("properties", {})


def _has_ref(prop_schema: dict) -> bool:
    """True when upstream says this property is a nested component,
    including the `Optional[Component]` shape (anyOf: [{$ref}, null])."""
    if "$ref" in prop_schema:
        return True
    return any("$ref" in choice for choice in prop_schema.get("anyOf", []))


def _annotation_has_basemodel(anno: Any) -> bool:
    """Walk an annotation (including Optional / Union) looking for a
    Pydantic BaseModel subclass."""
    if isinstance(anno, type) and issubclass(anno, BaseModel):
        return True
    for arg in get_args(anno):
        if _annotation_has_basemodel(arg):
            return True
    return False


def _assert_model_matches(
    model: type[BaseModel],
    upstream_schema: dict,
    *,
    endpoint: str,
) -> None:
    upstream_props = _properties(upstream_schema)
    for field_name, field_info in model.model_fields.items():
        assert field_name in upstream_props, (
            f"beacon-mcp {model.__name__} declares field '{field_name}' "
            f"but upstream {endpoint} has no such property.\n"
            f"Upstream properties: {sorted(upstream_props)}"
        )
        upstream_prop = upstream_props[field_name]
        if _has_ref(upstream_prop):
            assert _annotation_has_basemodel(field_info.annotation), (
                f"beacon-mcp {model.__name__}.{field_name} is annotated "
                f"as a scalar/primitive, but upstream {endpoint} declares "
                f"it as a nested object. This is the drift class that "
                f"broke beacon_get_job before beacon-mcp#3 — the field "
                f"must be typed as an Optional[BaseModel] instead."
            )


# ── Contract cases ────────────────────────────────────────────────────


def test_job_summary_matches_upstream_job_list_response(upstream_openapi):
    upstream = _resolve_ref("#/components/schemas/JobListResponse", upstream_openapi)
    _assert_model_matches(
        JobSummary, upstream, endpoint="GET /api/jobs (JobListResponse)"
    )


def test_job_detail_matches_upstream_job_detail_response(upstream_openapi):
    upstream = _resolve_ref("#/components/schemas/JobDetailResponse", upstream_openapi)
    _assert_model_matches(
        JobDetail, upstream, endpoint="GET /api/jobs/{id} (JobDetailResponse)"
    )


def test_score_matches_upstream_score_summary(upstream_openapi):
    upstream = _resolve_ref("#/components/schemas/ScoreSummary", upstream_openapi)
    _assert_model_matches(Score, upstream, endpoint="ScoreSummary (nested on score)")


def test_project_summary_matches_upstream_project_response(upstream_openapi):
    upstream = _resolve_ref("#/components/schemas/ProjectResponse", upstream_openapi)
    _assert_model_matches(
        ProjectSummary,
        upstream,
        endpoint="GET /api/profile/projects (ProjectResponse)",
    )
