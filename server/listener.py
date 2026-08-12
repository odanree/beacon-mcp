"""Event-driven RAG rebuild listener.

Runs a persistent `LISTEN rag_stale` connection to Beacon's Postgres.
When a project or experience is written (see Beacon migration 041),
a Postgres trigger fires `NOTIFY rag_stale, '<table>:<id>'`. This
process picks that up, coalesces bursts of NOTIFYs on a debounce
window, and triggers the same `refresh_chatbot_rag` build path that
the MCP tool uses.

Why standalone: `fastmcp` runs the MCP server as a stdio process
invoked on-demand by MCP clients. Adding a persistent LISTEN loop
inside that lifecycle doesn't fit — the MCP process comes and goes.
A separate long-running entry point does.

Usage
-----
Set `BEACON_DATABASE_URL` (raw asyncpg URL, no `+asyncpg` driver
suffix) plus the vars `refresh_chatbot_rag` needs
(`AI_CHATBOT_PATH`, `OPENAI_API_KEY`, `BEACON_API_URL`, `BEACON_JWT`),
then:

    beacon-mcp-listener

Or, without an install:

    python -m server.listener

Ctrl-C to stop.

Design notes
------------
- **Reconnect on connection loss.** If Postgres restarts, the network
  blips, or the pooler drops us, `asyncpg` raises. We back off and
  reconnect. `LISTEN` state is per-connection, so we re-issue it on
  every reconnect.
- **Debounce, not throttle.** Every NOTIFY resets the timer. If edits
  keep flowing, we don't rebuild until the stream quiets down. Bounded
  by MAX_QUIET_WAIT so a very chatty burst still yields a rebuild
  every 60s at worst.
- **One rebuild at a time.** A rebuild is a subprocess (bun + git);
  we serialize with an `asyncio.Lock`. NOTIFYs that land during a
  rebuild are folded into the next window.
- **Cancel-safe.** SIGINT tears down the listener + any pending
  rebuild task cleanly, without leaving orphaned subprocesses.

Deployment status and evolution
-------------------------------
**Where things run today:**

    ┌─────────────────────┐        ┌─────────────────────────┐
    │  Beacon Postgres    │        │  Operator's dev machine │
    │  (Hetzner VPS)      │        │                         │
    │                     │        │  ┌──────────────────┐   │
    │  ┌───────────────┐  │ NOTIFY │  │  listener.py     │   │
    │  │ AFTER-INSERT  │──┼────────┼──▶  (this file)     │   │
    │  │ trigger fires │  │        │  │                  │   │
    │  └───────────────┘  │        │  │  ├─ debounce 10s │   │
    │                     │        │  │  └─ refresh_     │   │
    │  ALWAYS ACTIVE      │        │  │     chatbot_rag()│   │
    │                     │        │  │      (bun + git) │   │
    │                     │        │  └──────────────────┘   │
    │                     │        │                         │
    │                     │        │  RUNS ONLY WHEN         │
    │                     │        │  OPERATOR STARTS IT     │
    └─────────────────────┘        └─────────────────────────┘

The triggers are always active on the production Postgres. The
listener runs on the operator's laptop because `refresh_chatbot_rag`
requires a local Bun runtime, a local ai-chatbot git checkout, and
local git-push credentials to trigger the Vercel deploy.

**The consequence:** NOTIFY is fire-and-forget. Signals that fire
while the listener isn't running are lost. A missed signal means the
RAG stays stale until the next NOTIFY lands with a running listener.
Acceptable for a portfolio-scale project where writes are infrequent
and the operator manually kicks a refresh when they notice drift.
Not acceptable for higher write-volume production.

**Evolution path when we care to close the gap:**

1. Move `data/knowledge.json` build INTO the ai-chatbot's Vercel build
   (needs `BEACON_JWT` as a Vercel env var).
2. Replace the `bun + git-push` path in `refresh_chatbot_rag()` with
   a POST to a Vercel Deploy Hook URL — same trigger, no local
   dependencies.
3. Deploy this listener as a systemd unit or Docker sidecar on the
   VPS where Beacon runs. Now it's always-on, the whole path is
   event-driven, and the operator's laptop is out of the loop.

That evolution isn't blocking anything today — the current shape is
the honest cost/benefit for a single-operator portfolio-scale system.
Named here so we can pick it up when write volume or availability
requirements shift.

**Interim mitigation available today (not implemented):** startup
reconciliation. On listener start, compare the newest `updated_at` on
`projects` / `experiences` against the timestamp of the last
committed `data/knowledge.json` on ai-chatbot and force a rebuild if
Beacon is ahead. Closes the "operator forgot to start the listener"
gap without changing the deployment shape. Skipped for now — the
manual refresh via `beacon_refresh_chatbot_rag` MCP tool is the
existing escape hatch and it works.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from dataclasses import dataclass, field
from typing import Any

try:
    import asyncpg
except ImportError:  # pragma: no cover
    print(
        "asyncpg is required for the listener. Install with:\n"
        "  pip install 'beacon-mcp[dev]' asyncpg\n"
        "or run `uv sync` in the beacon-mcp repo.",
        file=sys.stderr,
    )
    sys.exit(1)

from server.config import settings
from server.tools.chatbot import ChatbotRefreshError, refresh_chatbot_rag

log = logging.getLogger("beacon-mcp.listener")

CHANNEL = "rag_stale"
# Absolute upper bound on debounce — even if edits keep streaming in,
# we rebuild at least this often. Keeps a chatty afternoon from
# starving the RAG index forever.
MAX_QUIET_WAIT = 60.0
# Base delay before we retry a dropped connection. Grows on each
# consecutive failure, capped at MAX_RECONNECT_DELAY.
RECONNECT_BASE_DELAY = 1.0
MAX_RECONNECT_DELAY = 30.0


@dataclass
class RebuildScheduler:
    """Coalesces a stream of NOTIFYs into at most one in-flight rebuild.

    - `nudge()` records a NOTIFY and (re)arms the debounce timer.
    - When the timer fires, `_run_rebuild()` executes the rebuild.
    - If NOTIFYs arrive while a rebuild is running, the timer is
      armed again after the rebuild completes so we pick them up.
    """

    debounce_seconds: float
    max_quiet_wait: float = MAX_QUIET_WAIT
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _timer_task: asyncio.Task | None = None
    _first_nudge_at: float | None = None
    _pending_payloads: list[str] = field(default_factory=list)

    def nudge(self, payload: str) -> None:
        """Record a NOTIFY payload; (re)arm the debounce timer."""
        loop = asyncio.get_running_loop()
        now = loop.time()
        self._pending_payloads.append(payload)
        if self._first_nudge_at is None:
            self._first_nudge_at = now

        # If we've already been waiting past the max quiet window,
        # fire immediately — a chatty stream shouldn't starve the
        # rebuild.
        elapsed = now - self._first_nudge_at
        remaining = max(0.0, self.max_quiet_wait - elapsed)
        wait = min(self.debounce_seconds, remaining)

        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()

        self._timer_task = asyncio.create_task(self._fire_after(wait))

    async def _fire_after(self, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        await self._run_rebuild()

    async def _run_rebuild(self) -> None:
        # `refresh_chatbot_rag` is a subprocess pipeline (local mode) or an
        # HTTP POST (webhook mode); serializing ensures we never fire two
        # concurrent rebuilds. NOTIFYs arriving during a rebuild are
        # captured for the next window.
        async with self._lock:
            payloads = list(self._pending_payloads)
            self._pending_payloads.clear()
            self._first_nudge_at = None
            mode = settings.rag_refresh_mode
            log.info(
                "rag rebuild triggered by %d NOTIFY events (mode=%s, last: %s)",
                len(payloads),
                mode,
                payloads[-1] if payloads else "<empty>",
            )
            try:
                result = await refresh_chatbot_rag(mode=mode)  # type: ignore[arg-type]
            except ChatbotRefreshError as exc:
                log.error(
                    "rag rebuild failed (%s): %s",
                    exc.kind,
                    exc,
                    extra={"detail": exc.detail[-500:]},
                )
                return
            except Exception:
                log.exception("rag rebuild raised unexpectedly")
                return

            if mode == "webhook":
                job = result.get("deploy_hook_response", {})
                job_id = (job.get("job", {}) or {}).get("id", "<unknown>")
                log.info(
                    "rag rebuild dispatched to Vercel Deploy Hook — job id %s: %s",
                    job_id,
                    result.get("deploy_hint", "-"),
                )
            elif result.get("changed"):
                log.info(
                    "rag rebuild committed %s — Vercel deploy hint: %s",
                    result.get("commit_sha", "<unknown>")[:12],
                    result.get("deploy_hint", "-"),
                )
            else:
                log.info(
                    "rag rebuild produced no changes: %s",
                    result.get("message", "-"),
                )

    async def cancel_pending(self) -> None:
        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()
            try:
                await self._timer_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110 — cancellation cleanup: swallow anything the cancelled task raises
                pass


async def _listen_loop(scheduler: RebuildScheduler, stop_event: asyncio.Event) -> None:
    """Open a Postgres connection, LISTEN, and route NOTIFYs into the scheduler.

    Reconnects on connection loss with exponential backoff.
    """
    delay = RECONNECT_BASE_DELAY
    while not stop_event.is_set():
        conn: asyncpg.Connection | None = None
        try:
            log.info("connecting to Beacon Postgres for LISTEN rag_stale")
            conn = await asyncpg.connect(settings.beacon_database_url)
            await conn.add_listener(CHANNEL, _on_notify(scheduler))
            log.info("listening on channel '%s'", CHANNEL)
            delay = RECONNECT_BASE_DELAY  # reset backoff on healthy connect

            # Sleep forever, waking on stop_event.
            stopper = asyncio.create_task(stop_event.wait())
            try:
                await stopper
            finally:
                stopper.cancel()
        except (
            asyncpg.PostgresConnectionError,
            asyncpg.exceptions.ConnectionDoesNotExistError,
            OSError,
        ) as exc:
            log.warning("listener connection dropped: %s — reconnecting in %.1fs", exc, delay)
        except Exception:
            log.exception("listener error — reconnecting in %.1fs", delay)
        finally:
            if conn is not None:
                try:
                    await conn.close(timeout=5.0)
                except Exception:  # noqa: BLE001, S110 — connection already broken; close-time errors are noise
                    pass

        if stop_event.is_set():
            break

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=delay)
        except TimeoutError:
            pass
        delay = min(delay * 2, MAX_RECONNECT_DELAY)


def _on_notify(scheduler: RebuildScheduler):
    """Return the asyncpg listener callback bound to `scheduler`."""

    def _cb(connection: Any, pid: int, channel: str, payload: str) -> None:
        log.debug("NOTIFY %s payload=%r pid=%d", channel, payload, pid)
        scheduler.nudge(payload)

    return _cb


async def _run() -> None:
    if not settings.beacon_database_url:
        log.error(
            "BEACON_DATABASE_URL is not set. The listener needs a raw "
            "Postgres URL (e.g. postgresql://user:pw@host:5432/beacon) "
            "to LISTEN on channel '%s'.",
            CHANNEL,
        )
        sys.exit(2)

    mode = settings.rag_refresh_mode
    if mode == "local":
        if not settings.ai_chatbot_path or not settings.openai_api_key:
            log.error(
                "RAG_REFRESH_MODE=local but AI_CHATBOT_PATH or OPENAI_API_KEY "
                "is unset. Either set both (for local rebuild) or switch to "
                "RAG_REFRESH_MODE=webhook (needs VERCEL_DEPLOY_HOOK_URL)."
            )
            sys.exit(2)
    elif mode == "webhook":
        if not settings.vercel_deploy_hook_url:
            log.error(
                "RAG_REFRESH_MODE=webhook but VERCEL_DEPLOY_HOOK_URL is unset. "
                "Set it in .env or switch to RAG_REFRESH_MODE=local."
            )
            sys.exit(2)
    else:
        log.error(
            "RAG_REFRESH_MODE=%r is unknown — expected 'local' or 'webhook'.",
            mode,
        )
        sys.exit(2)

    scheduler = RebuildScheduler(debounce_seconds=settings.rag_debounce_seconds)
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()

    def _handle_signal(*_: object) -> None:
        log.info("shutdown signal received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler for SIGTERM.
            # SIGINT still works via KeyboardInterrupt below.
            pass

    log.info(
        "listener started (mode=%s, debounce=%.1fs, max_quiet_wait=%.1fs)",
        mode,
        settings.rag_debounce_seconds,
        MAX_QUIET_WAIT,
    )
    try:
        await _listen_loop(scheduler, stop_event)
    finally:
        await scheduler.cancel_pending()
        log.info("listener stopped cleanly")


def main() -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        # asyncio.run already ran the finally block; nothing else to do.
        pass


if __name__ == "__main__":
    main()
