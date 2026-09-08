# EevaResearch AI

An evidence-first, thematic market-intelligence and research tool covering
five frontier-technology themes: **AI Buildout, Humanoids, Space, Memory,
and Photonics**. It is not an autonomous trading agent and does not execute
trades — every material claim is labeled Fact, Interpretation, Inference, or
Uncertainty, and the app never says buy/sell/hold or gives a price target.

**This is the foundation phase.** Everything in this build is demo/mock
data, clearly labeled as such throughout the UI. There is no real ticker
universe, no paid APIs, no live news ingestion, no autonomous research
loops, no trading integrations, and no LLM wiring — see
`IMPLEMENTATION_NOTES.md` for exactly what's built vs. planned, and
`MIGRATION_NOTES.md` for what this branch replaced.

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Nothing needs to be configured to run — there are no required environment
variables in this phase (`.env.example` documents what's reserved for
later). Open the URL Streamlit prints (typically `http://localhost:8501`).

## Running tests

```bash
pytest -q
```

66 tests: data-model validation, repository/loader behavior, pure logic
helpers, and `st.testing.v1.AppTest`-based smoke tests covering every page.
No test makes a real network call.

### Local Postgres tests

A small number of tests (`tests/test_backend_factory_postgres.py`,
`tests/test_state_db_postgres_*.py`) exercise the isolated Postgres
backend (`src/data_access/postgres_state_db/`) against a real Postgres
connection. This is **local, disposable test infrastructure only** — it
is not Neon, not any hosted database, and not production infrastructure
of any kind. Without it, these tests skip cleanly and the rest of the
suite is unaffected.

`scripts/postgres_test_container.sh` starts exactly one loopback-only
Postgres container (`127.0.0.1:55432`, container name
`eevaresearch-postgres-test-phase4b`), runs whatever command you give it
with a fresh, in-memory-only test password available to that command,
and always removes the container afterward — no persistent volume is
ever created, and nothing is retained once the wrapped command exits.
The password is generated fresh on every run and supplied only through
the `EEVARESEARCH_PG_TEST_PASSWORD` environment variable for the
duration of the wrapped command; it is never printed, never written to
a file, and must never be placed in `.env` or committed anywhere.

Running the script — and therefore starting Docker — is a deliberate
action each time; the script itself does not authorize or automate
Docker use. Example usage (illustrative only):

```bash
scripts/postgres_test_container.sh \
  .venv/bin/python3 -m pytest tests/test_backend_factory_postgres.py -q
```

The existing pytest fixtures (`tests/_postgres_test_support.py`) create
and drop their own isolated schema per test against that one container
— no separate setup step is needed beyond having the container running
and the password variable set, both of which the script above handles
for you.

## Formatting / linting

No linter is enforced yet in this phase. If you add one, `ruff` (fast,
zero-config-friendly) is a reasonable default: `pip install ruff && ruff
check .`. Keep line length and import-sorting rules light — the codebase
currently has no linter-driven conventions beyond standard PEP 8.

## Deploying to Streamlit Community Cloud

1. Push this repository (or your fork) to GitHub.
2. At [share.streamlit.io](https://share.streamlit.io), create a new app
   pointing at this repo, branch, and `app.py` as the entry point.
3. No secrets are required for this phase — the "Secrets" panel can stay
   empty.
4. Streamlit Cloud auto-redeploys on every push to the connected branch.

## Project structure

```
app.py                       Entry point — st.navigation setup, page registration
requirements.txt
.env.example                 No required vars in this phase; documents Phase 2/3 vars
MIGRATION_NOTES.md            What the prior product was, what's retired, what's preserved
IMPLEMENTATION_NOTES.md       Done / mocked / Phase 2 & 3 plans
data/seed/                    Demo data — see data/seed/README.md
  README.md
  *.json                      One file per repository (themes, tickers, evidence, ...)
src/
  config/settings.py          App-wide config (no required env vars in this phase)
  models/models.py            Typed dataclasses — the whole data model
  data_access/
    interfaces.py              Repository ABCs — pages depend on these, never on JSON directly
    loaders.py                  Shared cached seed-JSON loader
    container.py                 The one place Phase 2 swaps demo -> real implementations
    demo/                        Phase 1 implementations, backed by data/seed/*.json
  logic/                       Pure helpers: formatting, theme/signal aggregation, claim-type mapping
  ui/
    theme.py                    Thin CSS layer over .streamlit/config.toml
    chrome.py                    Global status banner + footer, wraps every page
    components/                  Cards, badges, section headers, tables, filters, empty states, Market Brief
    pages/                       One module per page (Overview, Themes, Research Chat, ...)
tests/
  test_models.py, test_data_access.py, test_theme_metrics.py,
  test_formatting.py, test_evidence.py, test_app_smoke.py
  apptest_pages/                Small harness scripts AppTest.from_file runs per page
```

## Where real data integrations will be added later

Every page depends on the repository interfaces in
`src/data_access/interfaces.py` (`ThemeRepository`, `TickerRepository`,
`EvidenceRepository`, `CatalystRepository`, `SignalRepository`,
`MarketDataProvider`, `ResearchAnswerProvider`), not on `data/seed/`'s JSON
files directly. Phase 2 (real evidence/market-data sources) and Phase 3 (a
curated ticker universe) both mean adding new implementations of these same
interfaces and repointing `src/data_access/container.py`'s
`get_repositories()` — no page-rendering code changes required. Full detail
in `IMPLEMENTATION_NOTES.md`.

## Architecture notes worth knowing

- **Navigation** uses `st.navigation` with function-based `st.Page` objects
  (not file-based pages). The ticker-detail template is registered with
  `visibility="hidden"` so it's routable via `st.page_link`/query params
  without appearing in the primary seven-item nav.
- **Every page is wrapped** in `src/ui/chrome.py`'s `with_chrome()`, which
  renders the demo-data status banner and footer around the page body — a
  new page added later gets these automatically rather than needing to
  remember to call them.
- **No third-party charting library** — `st.bar_chart`/`st.dataframe` only,
  per the design brief for this phase.
