# ADR 001 — Refresh chatbot RAG index from Beacon via MCP tool

**Status:** Accepted, 2026-07-03.

**Decision:** `beacon-mcp` exposes a `beacon_refresh_chatbot_rag` tool that
takes the operator's Beacon profile, embeds it, commits the resulting
`data/knowledge.json` to a local `ai-chatbot` git checkout, and pushes.
Pushing to `ai-chatbot/main` triggers a Vercel deploy, so within ~2 minutes
the public chatbot on danhle.net serves grounded answers from the
just-refreshed corpus.

## Why not a scheduled Action

Two other options considered:

- **GitHub Action cron** (weekly rebuild in the ai-chatbot repo) — fires on
  a schedule regardless of whether anything changed. Requires the OpenAI
  key and the Beacon JWT as GitHub secrets. `BEACON_JWT` rotates every few
  weeks (browser session cookie), so it would silently 401 and skip the
  refresh until the operator noticed the drift and rotated the secret.

- **Cross-repo repository_dispatch** (portfolio push → ai-chatbot Action).
  Solves the "when to run" question well for portfolio-driven changes, but
  the operator explicitly wants a path where a project added to Beacon
  (never to the portfolio site) still lands in the chatbot. Portfolio push
  doesn't trigger when only Beacon changed.

The MCP tool is the right shape for "operator adds a project to Beacon and
also wants the chatbot updated" because it's the same session where the
operator is already talking to Beacon. `beacon_add_project` and then
`beacon_refresh_chatbot_rag` — both from the same conversation, both
authenticated by the same JWT that's already in the operator's local
`.env`.

The two other options aren't excluded; they can co-exist as safety-net
automation later.

## Design

The tool shells out to `npm run build:knowledge` in the operator-configured
`AI_CHATBOT_PATH` checkout, injecting three env vars:

- `BEACON_API_URL` + `BEACON_JWT` from this MCP's own `.env` — the build
  script sources projects + experiences from Beacon when both are set (see
  ai-chatbot ADR 002).
- `OPENAI_API_KEY` from this MCP's `.env` — embeddings.

After the build, `git diff --quiet -- data/knowledge.json` decides whether
a commit is needed:

- **Unchanged:** returns `{ok: true, changed: false}` — the operator can
  see nothing was pushed. Costs ~$0.0002 (embeddings) but no repo churn.
- **Changed:** `git add`, `git commit`, `git push`. Standard commit message
  `chore(rag): refresh knowledge.json from Beacon` unless the caller
  overrides.

Structured error envelope with three error kinds:

- `config` — env unset, path missing / not a git checkout, npm/git missing.
- `build` — the embed script failed. Detail includes the last 2 KB of
  stderr, so an expired JWT surfaces immediately as `HTTP 401 (JWT expired
  — refresh BEACON_JWT)` in the tail rather than a generic "npm exit 1".
- `git` — a stage / commit / push failure. Rare, usually a network issue
  or dirty working tree.

## What to watch for

- **Git push credentials.** The tool assumes the checkout has push access
  configured (via `gh auth login` or a credential helper). The MCP
  process doesn't manage credentials itself. If the push fails, the commit
  landed locally — the operator can push manually and no data is lost.
- **The `.env` file grows.** `beacon-mcp` now optionally holds
  `AI_CHATBOT_PATH` and `OPENAI_API_KEY`. Both stay empty by default so
  cloned forks don't accidentally enable the tool. Empty values fail the
  `config` check with a clear message.
- **JWT expiry surfaces here first.** Since this tool exercises Beacon
  more heavily than the read-only project tools (fetches ALL projects +
  experiences in a single call), an expired JWT will typically show up on
  the next `refresh_chatbot_rag` before the operator notices via
  `beacon_list_projects`.
- **Cost.** Each refresh is ~$0.0002 for embeddings — effectively free.
  No rate limiting needed on the tool.
