# EevaResearch AI

An evidence-first, thematic market-intelligence tool for frontier-technology
sectors. It autonomously ingests regulatory filings from the US (SEC EDGAR),
Korea (OpenDART) and Japan (EDINET) plus curated news feeds, detects material
disclosures against a typed theme taxonomy, extracts and translates the
supporting text, and surfaces the result as reviewable signals, research cases
and a daily news digest.

It is **not** a trading agent. It never says buy/sell/hold and never gives a
price target. Every material claim is labelled **Fact**, **Interpretation**,
**Inference** or **Uncertainty**, and nothing is published autonomously unless
it passes a deterministic evidence policy.

## Stack

- **Python 3.12** (`runtime.txt`), **Streamlit** UI (19 page modules, routed via `st.navigation`)
- **PostgreSQL** in production (schema v25); SQLite (v23) and JSON backends also
  implemented, selected by `EDGE_DB_BACKEND` via
  `src/data_access/backend_factory.py`
- **psycopg 3** driver
- **Google OAuth** sign-in (`st.login`), plus an email allowlist beta gate
- External: SEC EDGAR, OpenDART, EDINET, DeepL, RSS/Atom feeds
- Hosted on **Render** — one Streamlit web service, three background workers, a
  static landing site, and a Postgres datastore
- No payments or billing of any kind

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py          # http://localhost:8501
```

The app starts with no configuration: `EDGE_DB_BACKEND` defaults to `json` and
the UI reads `data/seed/`. Live ingestion, Postgres and OAuth all require
environment variables — see `.env.example` for names, and
`IMPLEMENTATION_NOTES.md` for which subsystem needs which.

The autonomous research agent has its own runtime, deliberately kept out of the
default install: `pip install -r requirements-agent.txt` (a superset) is needed
only to run its tests or its worker.

## Tests

```bash
pytest -q
```

~6,900 tests. A small number require a local Postgres container and skip
cleanly without one:

```bash
scripts/postgres_test_container.sh .venv/bin/python -m pytest tests/test_backend_factory_postgres.py -q
```

That script starts one loopback-only, volume-less container, supplies a
freshly-generated password through `EEVARESEARCH_PG_TEST_PASSWORD` for the
duration of the command, and always removes the container afterwards.

Two things to know before you read a failing run:

- **Many tests are diff-scope guards.** They run `git diff --name-only HEAD`
  and fail if a change touches files outside an approved set. They fail on *any*
  uncommitted tracked modification and clear once you commit — a dirty tree
  looks far more broken than it is.
- **A clean checkout is not currently fully green.** See
  `IMPLEMENTATION_NOTES.md` for the current list and why each one fails.

## Deploy

Push to `main` and Render auto-deploys. There is no `Procfile` or `Dockerfile`.

`render.yaml` records the five live services as they are configured today, so
the deployment is reviewable in the repository instead of only in the
dashboard. **It is a record, not an instruction:** every service was created by
hand, none belongs to a Blueprint instance, and Render therefore does not read
this file. Changing it changes nothing until someone deliberately creates a
Blueprint from it. Environment variables are listed by name with `sync: false`
— no value, secret or DSN is published there.

| Service | Type | Start command |
|---|---|---|
| `eevaresearch-ai` | Web | `bash scripts/render_start.sh` → Streamlit |
| `eevaresearch-radar-worker` | Worker | `python -m scripts.radar_worker` |
| `eevaresearch-daily-news-worker` | Worker | `python -m scripts.daily_news_worker` |
| `eevaresearch-company-discovery` | Worker | `python -u -m scripts.company_discovery_worker` |
| `eevaresearch-landing` | Static | publishes `landing/` |

`scripts/render_start.sh` generates `.streamlit/secrets.toml` from environment
variables before starting Streamlit, because `app.py` reads `st.user` on every
page load and Streamlit raises without an `[auth]` section.

Not every service tracks `main` today, and a suspended research-agent worker
also exists — `IMPLEMENTATION_NOTES.md` records the current drift.

## Where the docs live

| File | What it holds |
|---|---|
| `design/DECISIONS.md` | **The architectural record.** ~5,000 lines, one section per phase. The authoritative "why" for anything non-obvious. |
| `design/*.md` | Per-phase design and readiness documents — 18 tracked, plus ~30 written but never committed |
| `IMPLEMENTATION_NOTES.md` | What is built, what is partial, what is known-broken |
| `design/HANDOFF.md` | Onboarding summary for a new contributor or assistant |
| `MIGRATION_NOTES.md` | What the earlier product was and what was retired |
| `data/seed/README.md` | The demo dataset used when `EDGE_DB_BACKEND=json` |

Module docstrings carry a lot of the reasoning too, and are kept current —
there are no `TODO`/`FIXME` comments anywhere in `src/`, by convention.
