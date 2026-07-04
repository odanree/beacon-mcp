"""
`beacon_refresh_chatbot_rag` — rebuild the ai-chatbot's RAG index from Beacon
and push, so a project added to Beacon reaches the public chatbot without
touching the portfolio site.

Shells out to `npm run build:knowledge` in the operator-configured local
ai-chatbot checkout, passing this MCP's Beacon credentials + the operator's
OPENAI_API_KEY. If the resulting `data/knowledge.json` differs from what's
committed, stages + commits + pushes — Vercel auto-deploys on push to main.

Failure modes surface as structured error envelopes:
  - `config`: AI_CHATBOT_PATH / OPENAI_API_KEY unset, or path doesn't exist
  - `build`:  the embed script failed (usually OPENAI or BEACON API-side)
  - `git`:    stage / commit / push failed
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

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
) -> tuple[int, str, str]:
    """Run a subprocess, capturing stdout+stderr as text."""
    import os

    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out_b, err_b = await proc.communicate()
    return proc.returncode or 0, out_b.decode("utf-8", errors="replace"), err_b.decode("utf-8", errors="replace")


async def refresh_chatbot_rag(commit_message: str | None = None) -> dict:
    """Do the actual work. Returns a status dict (see main.py tool docstring)."""
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

    # Pick npm binary. On Windows Git Bash / PowerShell, `npm` resolves to
    # `npm.cmd`; asyncio's exec on Windows won't find `npm` alone.
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        raise ChatbotRefreshError("config", "npm not found on PATH.")
    git = shutil.which("git")
    if not git:
        raise ChatbotRefreshError("config", "git not found on PATH.")

    build_env = {
        "OPENAI_API_KEY": settings.openai_api_key,
        "BEACON_API_URL": settings.beacon_api_url,
        "BEACON_JWT": settings.beacon_jwt,
    }
    rc, out, err = await _run([npm, "run", "build:knowledge"], repo, build_env)
    if rc != 0:
        raise ChatbotRefreshError(
            "build",
            f"npm run build:knowledge exited with code {rc}",
            detail=(err or out)[-2000:],
        )

    # Was the index actually changed?
    rc, out, err = await _run(
        [git, "diff", "--quiet", "--", "data/knowledge.json"], repo,
    )
    if rc == 0:
        return {
            "ok": True,
            "changed": False,
            "message": "knowledge.json is byte-identical to HEAD — nothing to commit.",
            "build_output_tail": out[-800:] if out else "",
        }

    # Stage + commit
    rc, _, err = await _run([git, "add", "data/knowledge.json"], repo)
    if rc != 0:
        raise ChatbotRefreshError("git", "git add failed", detail=err[-2000:])

    msg = commit_message or "chore(rag): refresh knowledge.json from Beacon\n\nvia beacon-mcp refresh_chatbot_rag."
    rc, _, err = await _run([git, "commit", "-m", msg], repo)
    if rc != 0:
        raise ChatbotRefreshError("git", "git commit failed", detail=err[-2000:])

    # Capture the new SHA
    rc, sha_out, err = await _run([git, "rev-parse", "HEAD"], repo)
    commit_sha = sha_out.strip() if rc == 0 else ""

    # Push (assumes credentials configured via gh CLI or system keychain)
    rc, _, err = await _run([git, "push"], repo)
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
