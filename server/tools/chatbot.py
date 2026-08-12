"""
`beacon_refresh_chatbot_rag` — rebuild the ai-chatbot's RAG index from Beacon,
so a project added to Beacon reaches the public chatbot without touching the
portfolio site.

Two modes, chosen by the `mode` parameter:

  - `local` (default): shells out to `bun run src/knowledge/build.ts` in the
    operator-configured local ai-chatbot checkout, then commits + pushes if
    `data/knowledge.json` changed. Vercel auto-deploys on the push.
    Requires AI_CHATBOT_PATH + OPENAI_API_KEY + local git-push creds.

  - `webhook` (ADR-021 phase 2+): POSTs to a Vercel Deploy Hook URL. Vercel
    triggers a fresh build that regenerates knowledge.json against Beacon
    (via ai-chatbot's `vercel-build` script — see ADR-021 phase 1).
    Requires VERCEL_DEPLOY_HOOK_URL. No local checkout, no local Bun,
    no local git creds — this is the mode the Fargate listener uses.

Failure modes surface as structured error envelopes:
  - `config`:  required env / path missing for the chosen mode
  - `build`:   the local embed script failed (local mode only)
  - `git`:     stage / commit / push failed (local mode only)
  - `webhook`: Vercel Deploy Hook POST returned non-2xx (webhook mode only)

Future work (see ADR-021 alternatives-considered):
  HMAC signing on the webhook path. Vercel Deploy Hooks accept unsigned POSTs
  today — the URL itself is the auth token. If we ever put an API Gateway
  proxy in front (for rate limiting, IP allowlisting, or audit logging),
  the payload should carry an HMAC header signed with a shared secret so
  the proxy can verify the caller. TODO stub at the send-webhook boundary.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Literal

import httpx

from server.config import settings


class ChatbotRefreshError(Exception):
    def __init__(self, kind: str, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.kind = kind
        self.detail = detail


async def _run(
    cmd: list[str],
    cwd: Path,
    env_extra: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> tuple[int, str, str]:
    """Run a subprocess, capturing stdout+stderr as text.

    stdin is wired to DEVNULL so a child that tries to read (e.g. Windows
    git-credential-manager prompting for GitHub creds when there's no TTY)
    fails fast with EOF instead of hanging the MCP call. A timeout guards
    against any other silent stall — raises ChatbotRefreshError('timeout').
    """
    import os

    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        try:
            await proc.wait()
        except Exception:  # noqa: BLE001, S110 — subprocess already killed; wait errors are cleanup noise
            pass
        raise ChatbotRefreshError(
            "timeout",
            f"{cmd[0]} did not complete within {timeout:.0f}s — killed.",
            detail=f"cmd={cmd!r}",
        )
    return proc.returncode or 0, out_b.decode("utf-8", errors="replace"), err_b.decode("utf-8", errors="replace")


async def refresh_chatbot_rag(
    mode: Literal["local", "webhook"] = "local",
    commit_message: str | None = None,
) -> dict:
    """Rebuild the ai-chatbot's RAG index.

    Args:
        mode:
            "local"   — shell out to bun + git push in a local checkout.
                        Preserves the pre-ADR-021 behavior (default for
                        backward compatibility with any existing caller).
            "webhook" — POST to VERCEL_DEPLOY_HOOK_URL. Vercel handles the
                        rebuild via ai-chatbot's vercel-build script
                        (ADR-021 phase 1). No local dependencies.
        commit_message: Optional commit-message override (local mode only).

    Returns a status dict. Shape varies by mode:
        local mode:   {ok, changed, commit_sha?, commit_message?, deploy_hint?, message?}
        webhook mode: {ok, mode, deploy_hook_response, deploy_hint}
    """
    if mode == "webhook":
        return await _refresh_via_webhook()
    if mode == "local":
        return await _refresh_via_local(commit_message=commit_message)
    raise ChatbotRefreshError(
        "config",
        f"unknown mode {mode!r} — expected 'local' or 'webhook'.",
    )


async def _refresh_via_webhook() -> dict:
    """POST to a Vercel Deploy Hook. Returns a status dict.

    Fire-and-forget by design: we send the POST, Vercel enqueues a build,
    and we return the deploy job identifier. We don't wait for the deploy
    to finish — it typically takes 30–60 seconds. Caller can poll Vercel
    or watch the ai-chatbot repo for a fresh commit if they need
    completion confirmation.
    """
    if not settings.vercel_deploy_hook_url:
        raise ChatbotRefreshError(
            "config",
            "VERCEL_DEPLOY_HOOK_URL is not set — cannot trigger webhook rebuild. "
            "Set it in .env or unset mode='webhook' to fall back to local mode.",
        )

    # TODO(ADR-021 follow-up): if we ever put an API Gateway proxy in front
    # of the Deploy Hook (for rate limiting / IP allowlist / audit logging),
    # add HMAC signing here:
    #   digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    #   headers["X-Beacon-Signature"] = f"sha256={digest}"
    # For now the Deploy Hook URL is itself the auth token.
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(settings.vercel_deploy_hook_url)
    except httpx.HTTPError as exc:
        raise ChatbotRefreshError(
            "webhook",
            f"Vercel Deploy Hook POST failed at the transport layer: {exc}",
            detail=repr(exc),
        )

    if resp.status_code >= 400:
        raise ChatbotRefreshError(
            "webhook",
            f"Vercel Deploy Hook returned HTTP {resp.status_code}",
            detail=resp.text[-2000:],
        )

    # Vercel Deploy Hooks return a body like:
    # {"job": {"id": "...", "state": "PENDING", "createdAt": ...}}
    # We surface it verbatim so downstream logging can attribute the deploy.
    try:
        payload = resp.json()
    except ValueError:
        payload = {"raw": resp.text[:500]}

    return {
        "ok": True,
        "mode": "webhook",
        "deploy_hook_response": payload,
        "deploy_hint": (
            "Vercel is building the ai-chatbot from main. Fresh knowledge.json "
            "lands within ~30–60 seconds via vercel-build → build:knowledge."
        ),
    }


async def _refresh_via_local(commit_message: str | None = None) -> dict:
    """Original bun + git-push implementation (unchanged behavior)."""
    if not settings.ai_chatbot_path:
        raise ChatbotRefreshError(
            "config",
            "AI_CHATBOT_PATH is not set — cannot locate the ai-chatbot repo.",
        )
    if not settings.openai_api_key:
        raise ChatbotRefreshError(
            "config",
            "OPENAI_API_KEY is not set — the embedding step will fail.",
        )
    repo = Path(settings.ai_chatbot_path).expanduser().resolve()
    if not (repo / "package.json").exists():
        raise ChatbotRefreshError(
            "config",
            f"AI_CHATBOT_PATH points to '{repo}' but no package.json is there.",
        )
    if not (repo / ".git").exists():
        raise ChatbotRefreshError(
            "config",
            f"AI_CHATBOT_PATH '{repo}' is not a git checkout.",
        )

    # Resolve bun directly. The Claude Code-launched MCP subprocess inherits a
    # narrow PATH that often lacks the user's `~/.bun/bin`, so relying on
    # `npm run build:knowledge → bun` was hanging for 10+ minutes before
    # bailing. Bypass npm entirely and invoke bun with an absolute path.
    bun = shutil.which("bun") or shutil.which("bun.exe")
    if not bun:
        # Common Windows install location — Claude Code's env often misses it
        # despite the user having `bun` on their interactive shell PATH.
        home = Path.home()
        candidate = home / ".bun" / "bin" / "bun.exe"
        if candidate.exists():
            bun = str(candidate)
    if not bun:
        raise ChatbotRefreshError(
            "config",
            "bun not found on PATH or at ~/.bun/bin/bun.exe. "
            "Install bun (https://bun.sh) or add it to the MCP process's PATH.",
        )
    git = shutil.which("git")
    if not git:
        raise ChatbotRefreshError("config", "git not found on PATH.")

    build_env = {
        "OPENAI_API_KEY": settings.openai_api_key,
        "BEACON_API_URL": settings.beacon_api_url,
        "BEACON_JWT": settings.beacon_jwt,
    }
    rc, out, err = await _run(
        [bun, "run", "src/knowledge/build.ts"], repo, build_env, timeout=180.0,
    )
    if rc != 0:
        raise ChatbotRefreshError(
            "build",
            f"bun run src/knowledge/build.ts exited with code {rc}",
            detail=(err or out)[-2000:],
        )

    # Force non-interactive git for every subsequent step: no terminal prompt,
    # no GCM UI, no askpass helper. If credentials aren't already cached
    # (Windows Credential Manager / gh CLI), git push will fail fast instead
    # of hanging the MCP call waiting for a UI that has no TTY to bind to.
    git_env = {
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "Never",
        "GIT_ASKPASS": "",
    }

    # Was the index actually changed?
    rc, out, err = await _run(
        [git, "diff", "--quiet", "--", "data/knowledge.json"], repo, git_env, timeout=15.0,
    )
    if rc == 0:
        return {
            "ok": True,
            "changed": False,
            "message": "knowledge.json is byte-identical to HEAD — nothing to commit.",
            "build_output_tail": out[-800:] if out else "",
        }

    # Stage + commit
    rc, _, err = await _run([git, "add", "data/knowledge.json"], repo, git_env, timeout=15.0)
    if rc != 0:
        raise ChatbotRefreshError("git", "git add failed", detail=err[-2000:])

    msg = commit_message or "chore(rag): refresh knowledge.json from Beacon\n\nvia beacon-mcp refresh_chatbot_rag."
    rc, _, err = await _run([git, "commit", "-m", msg], repo, git_env, timeout=30.0)
    if rc != 0:
        raise ChatbotRefreshError("git", "git commit failed", detail=err[-2000:])

    # Capture the new SHA
    rc, sha_out, err = await _run([git, "rev-parse", "HEAD"], repo, git_env, timeout=10.0)
    commit_sha = sha_out.strip() if rc == 0 else ""

    # Push (assumes credentials configured via gh CLI or system keychain)
    rc, _, err = await _run([git, "push"], repo, git_env, timeout=60.0)
    if rc != 0:
        raise ChatbotRefreshError(
            "git",
            "git push failed — commit landed locally but did not reach origin. Resolve and push manually.",
            detail=err[-2000:],
        )

    return {
        "ok": True,
        "changed": True,
        "commit_sha": commit_sha,
        "commit_message": msg.splitlines()[0],
        "deploy_hint": "Vercel auto-deploys on push to main; the chatbot picks up the new index within ~1-2 minutes.",
    }
