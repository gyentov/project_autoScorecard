# 🌾 AutoScorecard

A credit-scorecard automodeler with a cozy pixel-art face. Four agents
(Partner, Prepper, Modeller, Deck Creator) turn your raw data into a scorecard
and an English presentation deck, while you make the key decisions in chat.

## Status
- **Phase 0 (done):** config, multi-provider LLM layer, budget ledger, project store + audit log, API skeleton
- **Phase 1 (done):** auditable scorecard engine (pure Python) - see `autoscorecard/engine/`
- **Phase 2a (done):** the office: Laras, Bening and Citra run kickoff → data → target → waterfall plan → Cut 1 → Cut 2 (→ extensions) in visual-novel meetings, with choices plus free typing, proposals that need ACC, MOM emails, honest cost estimates, and an event stream. See [docs/office.md](docs/office.md)
- Phase 2b: pixel office driven by the real events · Phase 3: decks (Kirana) · Phase 4: game UI · Phase 5: polish

## Setup
```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env        # add whichever keys you have; missing ones are skipped
.venv/bin/python -m pytest  # no keys needed
```

## Useful commands
```bash
.venv/bin/uvicorn autoscorecard.server.app:app   # the pixel office: open http://127.0.0.1:8000/
.venv/bin/python scripts/office_cli.py --demo   # the office in the terminal (demo data)
.venv/bin/python scripts/llm_smoke.py           # which providers are usable
.venv/bin/python scripts/llm_smoke.py --call    # one tiny test call per provider
.venv/bin/uvicorn autoscorecard.server.app:app --reload   # API on :8000, docs at /docs
```

## LLM providers & budget (`config/config.yaml`)
- `anthropic` → Claude (prompt caching on). `openai_compat` → OpenAI, Gemini, OpenRouter, Ollama.
- `offline` → no LLM, $0, always available; the pipeline still runs on default recommendations.
- Each agent has a first-choice provider; on failure the router walks `fallback_chain`.
  Out of credit → that provider is disabled for the session. Rate limit → wait and retry.
- `budget.total_usd` / `per_project_usd` are hard stops for **paid** providers only.
- Every call is logged in `data/ledger/usage.jsonl` (tokens + USD).

## Scorecard engine (`autoscorecard/engine/`)
Written in plain Python with numpy/pandas only, so every number can be traced. y = 1 means BAD.
| module | what it does |
|---|---|
| `data.py` | load csv/xlsx/parquet, profile columns, suggest join keys, `safe_join` (aggregates 1:many, never multiplies rows), target candidates and `make_target`, stratified train/test + out-of-time split |
| `binning.py` | quantile fine classing, then ChiMerge to min bin size / **strictly monotonic** bad rate (direction chosen by IV) / max bins; separate missing bin; categorical rare-pooling; WoE = ln(%good/%bad), IV; manual bins; every merge logged |
| `selection.py` | IV filter; hierarchical clustering of the WoE correlation matrix (single/complete/average linkage, d = 1-\|corr\|), keep highest IV per cluster; forward / backward / stepwise (LR test to enter, Wald to remove, negative-coefficient check); VIF |
| `logistic.py` | logistic regression by IRLS with standard errors, z, p-values, AIC/BIC |
| `scorecard.py` | PDO scaling (default 600 points = 50:1 odds, 20 PDO), points per attribute, scoring, save/load JSON |
| `metrics.py` | AUC, Gini, KS, gains table, PSI |
| `steps/` | the **step registry**: one registered function per waterfall step (built-ins here, custom ones in `plugins/steps/`) |
| `waterfall.py` | the **modelling waterfall**: ordered, configurable selection steps (missing rate → identical rate → IV → correlation clustering → stepwise → forward → backward → finalization), plan save/approve |
| `waterfall_report.py` | plan diagram (SVG + Mermaid) shown before modelling; waterfall report (Markdown + SVG) after |
| `dictionary.py` | data dictionary of the raw data (descriptions, variable types, lineage of derived columns, counts, nulls, min/max) and outlier detection / treatment |
| `finalization.py` | forced features, manual bin / WoE edits, and the draft → confirmed step for each build |
| `pipeline.py` | `build_scorecard`: split → waterfall → scaling → validation; writes the audit log and `artifacts/` |

Docs: [modelling waterfall](docs/modelling_waterfall.md) · [binning](docs/binning.md) · [data dictionary & outliers](docs/data_dictionary.md) · [custom waterfall steps](docs/extending.md) · [example report](docs/examples/waterfall_report.md)

```bash
.venv/bin/python scripts/demo_scorecard.py         # full build on synthetic data
.venv/bin/python scripts/demo_scorecard.py --docs  # ...and refresh docs/examples/
```

## Layout
```
autoscorecard/
  config.py          settings + env keys
  llm/               types, providers, router, cost ledger, rate limiter
  store.py           projects/<id>/{raw,work,artifacts,audit.jsonl}
  engine/            Phase 1 – scorecard maths (no LLM)
  office/            Phase 2a – the team, meetings, inbox, proposals, costs, events
  server/app.py      FastAPI
config/config.yaml   providers, agent→provider map, budget
scripts/             smoke tests
plugins/steps/       custom waterfall steps (example: exclude_by_policy.py)
frontend/, assets/   Phase 4
```

## Data privacy
Agents only ever see metadata and aggregates (schemas, statistics, bin tables).
Row-level data stays on your machine and is handled by the engine.
