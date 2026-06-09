"""
Tiny typed wrapper around the Beacon HTTP API.

All endpoints are JWT-authenticated (Beacon uses Google OAuth → JWT;
the user pastes the access_token cookie value into BEACON_JWT). On 401
we raise a clear error pointing back to the cookie grab — nothing the
LLM can usefully retry on its own.
"""

from __future__ import annotations

import logging

import httpx

from server.config import settings

log = logging.getLogger(__name__)

TIMEOUT_S = 20.0


class BeaconAuthError(RuntimeError):
    """Raised on 401 — BEACON_JWT missing or expired."""


class BeaconHTTPError(RuntimeError):
    """Raised on any other non-2xx — wraps the upstream error."""


def _headers() -> dict[str, str]:
    if not settings.beacon_api_url:
        raise BeaconAuthError(
            "BEACON_API_URL is empty. Set it to your Beacon deployment URL "
            "in .env (e.g. https://beacon.your-domain.com), then restart "
            "the MCP server."
        )
    if not settings.beacon_jwt:
        raise BeaconAuthError(
            "BEACON_JWT is empty. Sign in to your Beacon instance in a "
            "browser, then DevTools → Application → Cookies → copy the "
            "access_token value into .env and restart the MCP server."
        )
    return {
        "Authorization": f"Bearer {settings.beacon_jwt}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def request(
    method: str,
    path: str,
    *,
    json: dict | None = None,
    params: dict | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict | list:
    """Issue one Beacon API call. `path` is appended to BEACON_API_URL."""
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=TIMEOUT_S)
    try:
        url = f"{settings.base}{path}"
        try:
            r = await client.request(method, url, json=json, params=params, headers=_headers())
        except httpx.RequestError as e:
            raise BeaconHTTPError(f"network error talking to {url}: {e}") from e
        if r.status_code == 401:
            raise BeaconAuthError(
                "Beacon returned 401 — BEACON_JWT is expired. "
                "Sign back in and re-grab the access_token cookie."
            )
        if r.status_code >= 400:
            raise BeaconHTTPError(f"{r.status_code} from {url}: {r.text[:300]}")
        if r.status_code == 204 or not r.content:
            return {}
        return r.json()
    finally:
        if own_client:
            await client.aclose()
