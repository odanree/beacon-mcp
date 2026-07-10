# beacon-mcp

**MCP server wrapping the [Beacon](https://github.com/odanree/job-search-pipeline) job-search-pipeline REST API** so Claude Code can add projects, list/inspect jobs, and (eventually) drive the resume generator without me ever opening a terminal to write `curl` and a JWT heredoc again.

`MCP` · `FastMCP` · `Beacon` · `Claude Code` · `Pydantic v2` · `httpx` · `respx`

| Tool | What it does |
|---|---|
| `beacon_add_project` | POST `/api/profile/projects` — capture a project for resume generation |
| `beacon_list_projects` | GET `/api/profile/projects` — see what's already on the profile |
| `beacon_update_project` | PATCH `/api/profile/projects/{id}` — edit an existing project |
| `beacon_refresh_chatbot_rag` | Rebuild the ai-chatbot's RAG index from Beacon and push (see [ADR 001](docs/adr/001-refresh-chatbot-rag.md)) — a project added here shows up in the public chatbot on danhle.net within ~2 min, no portfolio-site change required |
| `beacon_list_jobs` | GET `/api/jobs` filtered by pipeline `status` |
| `beacon_get_job` | GET `/api/jobs/{id}` — full record + JD text |

Plus a companion **long-running listener** — see [Event-driven RAG rebuilds](#event-driven-rag-rebuilds) below.

## Why this exists

I added three portfolio projects to my Beacon profile during a single Claude session. Each one was 80 lines of curl + JSON-body + JWT-cookie-from-DevTools + bash-heredoc-syntax-vs-PowerShell. Three times. With encoding errors and reshuffled apostrophes.

This server reduces every one of those incidents to:

> "Add a Beacon project — name: infra-mcp, url: https://github.com/odanree/infra-mcp, tech stack: MCP / FastMCP / pytest, outcome: …"

The model fills the schema, the tool POSTs, done.

## Auth

Beacon uses Google OAuth → JWT. Easiest way to get the token:

1. Sign in to your Beacon instance in the browser
2. DevTools → Application → Cookies → copy the value of `access_token`
3. Paste into `.env` as `BEACON_JWT=…` (and set `BEACON_API_URL` to your deployment)

When the token expires (typically 24h–30d depending on your config) any tool call returns `{"ok": false, "error_kind": "auth", "error": "..."}` and the model knows to ask you to refresh. The error message includes the exact step to do so.

## Install

```bash
git clone https://github.com/odanree/beacon-mcp
cd beacon-mcp
python -m venv .venv
.venv/Scripts/activate
pip install -e ".[dev]"
cp .env.example .env       # paste BEACON_JWT
```

## Register with Claude Code (user-scope)

```bash
claude mcp add beacon -s user -- "/abs/path/to/beacon-mcp/.venv/Scripts/python.exe" -m server.main
claude mcp list
```

Try:

> "List my Beacon projects."
>
> "Add a new Beacon project — name 'beacon-mcp', url 'https://github.com/odanree/beacon-mcp', tech stack ['MCP', 'FastMCP', 'pytest-asyncio'], outcome '6 tools, 16 tests (12 unit + 4 upstream-schema contract), replaces the curl+JWT+heredoc dance every time I want to update my resume profile.'"
>
> "List my jobs in the screen stage."

## Tests

```bash
pytest
# All HTTP mocked via respx — no network calls, no real Beacon hits.
```

16 tests covering:
- Authenticated POST / PATCH flows (project create + partial update)
- 401 vs 5xx error mapping (clear error kinds — `auth` vs `http`)
- Missing JWT detection at the client boundary
- Pagination + status filter param passing
- Both envelope shapes Beacon endpoints can return (`[…]` and `{items: […]}`)
- Upstream-schema contract test that pulls Beacon's `api/openapi.json` and verifies our pydantic response models still match — catches renamed fields or scalar-vs-nested-object drift at PR time (skips gracefully when the upstream schema URL is unreachable)

## Event-driven RAG rebuilds

The MCP tool `beacon_refresh_chatbot_rag` triggers a rebuild on demand. The listener at [`server/listener.py`](server/listener.py) makes it event-driven — you don't have to remember to refresh.

### How the loop works

1. **DB triggers** (Beacon migration [`041_notify_rag_stale.py`](https://github.com/odanree/job-search-pipeline/blob/master/migrations/versions/041_notify_rag_stale.py)) fire `NOTIFY rag_stale, '<table>:<id>'` after every INSERT / UPDATE / DELETE on `projects` and `experiences`. Always active on the production Postgres.
2. **Listener** (`server/listener.py`) holds a persistent `LISTEN rag_stale` connection, coalesces bursts on a 10s debounce, and delegates to the same `refresh_chatbot_rag()` function the MCP tool uses.
3. **Rebuild** runs `bun run src/knowledge/build.ts` in your local ai-chatbot checkout, commits `data/knowledge.json` if it changed, and pushes → Vercel deploys.

### Running the listener

```bash
# One-time — add the raw asyncpg URL to your .env
echo "BEACON_DATABASE_URL=postgresql://user:pw@your-vps:5432/beacon" >> .env

# Then, in a terminal you're willing to leave open:
beacon-mcp-listener
# or:  python -m server.listener
```

Ctrl-C to stop.

Requires the same `AI_CHATBOT_PATH` + `OPENAI_API_KEY` as the manual `beacon_refresh_chatbot_rag` MCP tool, plus:

| Env var | Purpose |
|---|---|
| `BEACON_DATABASE_URL` | Raw asyncpg URL to Beacon's Postgres (no `+asyncpg` driver suffix). Needed to `LISTEN`. |
| `RAG_DEBOUNCE_SECONDS` | Optional. How long the listener coalesces a burst before rebuilding. Default `10`. |

### Deployment status — honest read

**Where things run today:**

| Component | Where | When active |
|---|---|---|
| DB triggers + NOTIFY channel | Beacon Postgres (VPS) | Always |
| `server/listener.py` | Wherever you start it — today, the operator's laptop | Only while running |
| `refresh_chatbot_rag()` | Same laptop (needs local Bun + git checkout + push creds) | On demand |

The triggers are always on. The listener runs on the operator's dev machine because the rebuild pipeline itself is local — moving the listener to a VPS without moving the rebuild there too would give you a listener that catches events but can't act on them.

**The gap:** NOTIFY is fire-and-forget. If the listener isn't running when a signal fires, that signal is lost. The RAG stays stale until the next NOTIFY arrives with a running listener — or until the operator runs `beacon_refresh_chatbot_rag` manually. Acceptable for a single-operator portfolio-scale project where writes are infrequent. Not the shape you'd ship for higher write-volume production.

### Evolution path when we care to close the gap

1. Move the `data/knowledge.json` build INTO ai-chatbot's Vercel build (needs `BEACON_JWT` as a Vercel env var).
2. Replace the `bun + git-push` path in `refresh_chatbot_rag()` with a POST to a Vercel Deploy Hook — same trigger effect, zero local dependencies.
3. Redeploy the listener as a systemd unit or Docker sidecar alongside Beacon on the VPS. Now it's always-on, fully event-driven, operator's laptop out of the loop.

Named here so the migration path is obvious when write volume or availability requirements shift. Not blocking anything today — the current shape is the honest cost/benefit for the current scale.

## Roadmap

- `beacon_search_jobs(query)` — natural-language job search, once Beacon's search endpoint stabilizes
- `beacon_generate_resume(job_id)` — kick the resume generator for one JD and return the markdown
- `beacon_score_resume(jd_text)` — score arbitrary JD text against the user's profile (good for quick "should I apply?" calls)
- Move JWT from .env to keyring storage on the host

## License

MIT
