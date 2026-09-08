# Demo seed data

Every file in this directory is **demonstration data only** — not real
market data, not derived from any real company's actual financials, filings,
or trading activity. It exists to exercise the application's UI, data model,
and repository layer during the foundation phase (see `MIGRATION_NOTES.md`
and `IMPLEMENTATION_NOTES.md` at the repo root).

| File | Backs |
|---|---|
| `themes.json` | The five fixed themes and their value-chain subcategories |
| `tickers_demo.json` | The single fictional demo ticker (`DEMO` / Nova Aperture Systems) |
| `evidence.json` | Demo evidence items shown across theme pages and Overview |
| `catalysts.json` | Demo catalyst-calendar entries |
| `signals.json` | Demo Signal Board entries |
| `rotation_metrics.json` | Demo Capital Rotation theme-level metrics |
| `chat_demo_answers.json` | Canned Research Chat answers for the suggested questions |
| `watchlists.json` | Seed rows for the four demo watchlists |

Conventions followed throughout, on purpose:

- Every evidence-bearing record sets `source_name` to `"EevaResearch Demo
  Data"` and omits `source_url` entirely — never a placeholder link that
  could be mistaken for a real, working source.
- No fabricated real-looking company names, analyst names, executive quotes,
  filings, or statistics anywhere in this directory.
- The one ticker (`DEMO`) is clearly labeled `"Nova Aperture Systems (Demo
  Company — Not Real)"` and its fundamental/technical fields are either
  categorical placeholders (e.g. `"Mid — placeholder"`) or rendered as an
  explicit `—` in the UI, never an invented price or valuation figure.

Loaded exclusively through `src/data_access/loaders.py` and the `demo/*`
repository implementations behind `src/data_access/interfaces.py` — nothing
in `src/ui/` reads these files directly. That's the seam Phase 2 uses to
swap this directory for a real data source.
