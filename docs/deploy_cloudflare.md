# Cloudflare version

The same app, hosted on Cloudflare. The normal local mode (venv + uvicorn, see the
README) keeps working unchanged; this is a second way to run it.

## How it works

```
 browser (behind Cloudflare Access: only your email gets in)
 ├─ page: the office UI (autoscorecard/server/static, unchanged)
 ├─ service worker (web/sw.js): catches every /api/... request
 └─ Python worker (web/py-worker.js): Pyodide runs autoscorecard + FastAPI
      projects saved in this browser (IndexedDB); row data never leaves it
      │  LLM calls only: POST /llm/chat
      ▼
 Cloudflare Pages Function functions/llm/[[path]].js   (same site, same Access login)
      │  service binding (no public URL)
      ▼
 Worker "autoscorecard-llm" (worker/): holds the API keys, checks provider/model/
 max_tokens against config.yaml, re-checks the Access token, logs cost to D1,
 refuses paid calls over budget.total_usd
```

- **Pyodide** is Python compiled for the browser (v314.0.7: Python 3.14, numpy, pandas,
  pyarrow, pydantic, FastAPI). The first visit downloads it (about 30-60 s), then the
  browser caches it.
- **Your data stays in your browser.** Uploads and projects are stored in that browser's
  IndexedDB. Another browser or computer starts with just the demo project, and clearing
  site data erases them. Storage is per address: `<hash>.autoscorecard.pages.dev` (the
  link of one deployment) is a different address from `autoscorecard.pages.dev`, so its
  projects are separate. Share the main address only.
- **Offline mode says why.** If the team answers without LLM, the reason is shown at the
  bottom of the page (e.g. `access denied: email not allowed`) and in the agents' reply;
  "kuota habis" means the budget or a provider's credit is really used up.
- **Only the Worker sees API keys.** The browser asks the Worker which key *names* exist
  and never receives a value.
- **Long jobs block the page while they run.** Model builds run inside the request
  (`app.inline_jobs`), because the browser has no threads, so the office is busy until
  the build finishes.
- **Budget:** the Worker keeps the shared total in D1 (`budget.total_usd`). Laras's
  per-project estimates still work per browser, as before.

## Files

| file | what |
|---|---|
| `package.json` | wrangler and yaml as dev dependencies, npm scripts |
| `scripts/build_web.mjs` | `npm run build` → `dist/` (Node only); also writes `worker/src/config.generated.js` from `config/config.yaml` |
| `web/` | `boot.js` (page), `sw.js` (service worker), `py-worker.js` (Pyodide), `_headers`, `wheels.json` (pure-Python wheels, sha256-pinned) |
| `autoscorecard/web/` | Python side: `boot.py` (browser config, start), `bridge.py` (calls the FastAPI app without a network) |
| `autoscorecard/llm/proxy.py` | LLM provider that sends the request JSON to the Worker |
| `wrangler.toml` | the Pages project `autoscorecard` and its service binding `LLM` |
| `functions/llm/[[path]].js` | Pages Function: `/llm/*` → the Worker |
| `worker/` | the Worker: `wrangler.toml`, `src/index.js`, `src/access.js`, tests |

## Run it locally

Needs Node 20+ (and Python 3.11+ for the normal local mode and the tests).

```bash
npm ci                     # wrangler + yaml, exact versions from package-lock.json
npm run build              # -> dist/
npm run dev:worker         # terminal 1: the LLM Worker on :8787 (Access check off, local D1)
npm run dev:pages          # terminal 2: the site on http://localhost:8788
```

Open http://localhost:8788. Without keys the team runs offline ($0). To try real LLM calls
locally, put keys in `worker/.dev.vars` (one `NAME=value` per line). The file is
gitignored; never commit it. `dev:pages` deliberately ignores the repo's `.env`, so keys
only ever reach the Worker.

Tests: `npm run test:worker` (Worker) and `.venv/bin/python -m pytest` (Python, includes
`tests/test_web.py`).

## Secrets for the Worker

Set each in the dashboard (**Workers & Pages → autoscorecard-llm → Settings → Variables and
Secrets → Add**, type **Secret**), or from a terminal with
`npx wrangler secret put <NAME> -c worker/wrangler.toml`. A provider whose key is missing
is skipped; offline mode always works.

| name | what |
|---|---|
| `ANTHROPIC_API_KEY` | Claude (claude_haiku, claude_sonnet) |
| `GEMINI_API_KEY` | Gemini free tier |
| `OPENAI_API_KEY` | OpenAI |
| `OPENROUTER_API_KEY` | OpenRouter |
| `ACCESS_TEAM_DOMAIN` | your Zero Trust team domain, e.g. `myteam.cloudflareaccess.com` |
| `ACCESS_AUD` | the Audience (AUD) tag of the site's Access application |
| `ALLOWED_EMAILS` | who may use the LLM: emails, comma-separated; `@company.com` allows a whole domain. The Worker re-checks it, so **everyone in the Access policy must be here too**, or they get offline mode. Leave it unset to trust the Access policy alone |

Without `ACCESS_TEAM_DOMAIN` and `ACCESS_AUD` the deployed Worker refuses every call: it
fails closed.

## Deploy (one time, all in the Cloudflare dashboard)

Nothing has to run on your computer: Cloudflare builds and deploys from GitHub.

1. **Storage & Databases → D1 → Create Database** named `autoscorecard`. Its id goes in
   `worker/wrangler.toml` (already set for this project). The `usage` table creates
   itself on first use.
2. Merge the code into `main`.
3. **Workers & Pages → Create → Workers → Import a repository**: this repo, Worker name
   `autoscorecard-llm` (must match `worker/wrangler.toml`), root directory `/`, deploy
   command `npm run deploy:worker`.
4. Add the secrets above to that Worker.
5. **Workers & Pages → Create → Pages → Connect to Git**: this repo, production branch
   `main`, build command `npm ci && npm run build`, output directory `dist`. The service
   binding to the Worker comes from `wrangler.toml`.
6. **Zero Trust → Access → Applications → Add → Self-hosted**: hostnames
   `autoscorecard.pages.dev` and `*.autoscorecard.pages.dev` (preview deployments), with a
   policy that allows only your email. Copy its AUD tag into the `ACCESS_AUD` secret.

From then on, every push to `main` redeploys both the site and the Worker. The Worker has
no public URL (`workers_dev = false`), so the only way in is through the site, which
Access guards; the Worker also verifies Access's token itself.
