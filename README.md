# RFM Studio

Internal tool for RFM analysis of client transaction data: connect the client's tables in BigQuery,
check and trim the data, and get a shareable report with segments, cohorts and market comparison.

Status: **engine, web app and deployment files are done.** Deploy it with [`docs/DEPLOY.md`](docs/DEPLOY.md).
Data source: BigQuery (CSV upload via Cloud Storage is planned). Product categories (L3) come later.

## Repository layout

| Path | What it is |
|---|---|
| `rfm_engine/` | The analysis engine. Pure Python + pandas, no web or cloud code. |
| `app/` | The web app: FastAPI server (`main.py`, `runs.py`, `sources.py`, `storage.py`) and the front end in `app/web/`. |
| `docs/DEPLOY.md` | Click-by-click Cloud Run deployment guide. |
| `Dockerfile` | One image for both Cloud Run services (the app and the share-link service). |
| `tests/` | Engine tests, including a check that the numbers match the original Colab notebook. |
| `sample_data/` | Dummy client tables (transactions, customers, items, fx_rates) used by the tests. |
| `notebooks/` | The original Colab notebooks the engine was ported from. |
| `prototype/` | Clickable design prototype in Precis branding (example data). |
| `scripts/run_engine.py` | Run the engine on a folder of CSVs from the command line. |

## The engine

```
rfm_engine/
  spec.py        field spec, default segment rules, segment descriptions
  config.py      RunConfig — every setting of a run, with validation (and the safe segment-rule checker)
  load.py        normalises raw tables, suggests the column mapping
  quality.py     L0 data check: mapping, cleaning, FX, trims, identity linking, items check
  customers.py   one row per customer: R/F/M inputs, enrichment, outliers, market / channel / loyalty, acquisition
  scoring.py     L1: cut-offs, scores, segments, segment KPIs
  cohorts.py     L5: retention, revenue per customer over time, time to 2nd purchase, monthly development
  views.py       the data behind each report tab, precomputed for every filter combination (+ L2 markets, insights)
  pipeline.py    run_check() for the data-check step, run_report() for the report + downloads
```

Two entry points:

```python
from rfm_engine import RunConfig, run_check, run_report

cfg = RunConfig(client_name="Client", snapshot_date="2026-11-01", reporting_currency="SEK")
check = run_check(tables, cfg)       # step 03: findings, descriptive stats, trim options
result = run_report(tables, cfg)     # step 04: result.report (JSON for the page), result.exports (CSV/JSON files)
```

### Changing the logic

* **Segment rules, bins, outliers, cohorts, trims** are settings in `RunConfig` — no code change needed.
* **A new data-quality check:** add a `dq.issue(...)` call in `quality.py`; it appears in the findings table automatically.
* **A new metric or chart:** compute its data in the matching section builder in `views.py`
  (`overview_section`, `segments_section`, `markets_section`, or `cohorts.py`), then add one entry to
  `app/web/static/js/charts.js` (`RFM.CARDS`) and put its id in `RFM.LAYOUT` under a tab. The header of that file
  shows the template. Filters, hover, "How to read", "View as table" and the shared page all work automatically.
* Run the tests after every change: `pytest -q`.

## Running locally

```bash
python -m venv .venv
```

```bash
.venv/bin/pip install -r requirements-dev.txt
```

```bash
.venv/bin/pytest -q
```

```bash
.venv/bin/python scripts/run_engine.py sample_data --snapshot 2026-11-01 --client "Dummy Nordic Retail" --out out/
```

Run the app locally with the sample data offered as a source:

```bash
RFM_STORAGE=./data RFM_LOCAL_DATA_DIR=. .venv/bin/uvicorn app.main:app --reload
```

Then open http://localhost:8000. Runs are stored in `./data` (git-ignored).

## How the app is put together

* **Steps:** Connect (BigQuery tables + column mapping) → Settings → Data check (findings, descriptive stats, trims)
  → Report (Overview, Segments, Cohorts, Markets; Product categories later).
* **Runs** are saved in the bucket under `runs/<id>/`, so a page reload never loses work; jobs run in the background
  and the page shows progress. Source tables are cached per run and re-read only when they change in BigQuery.
* **The report** is one JSON document with every filter combination precomputed. The same front-end code renders it
  in the app and in the self-contained shared page (`/r/<token>`), served by a separate public service.

The notebook reference numbers in `tests/reference/notebook_reference.json` were produced with
`python tests/reference/run_notebook.py notebooks/RFM_Descriptive_Cohorts.ipynb`.
