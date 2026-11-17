# RFM Studio

Internal web app for RFM analysis of client transaction data. Analysts connect the client's BigQuery tables, review a
data-quality check, trim the data if needed, and get a report (segments, cohorts, markets) they can download or
share with a link.

* **Using the app:** [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md)
* **Deploying it on Google Cloud:** [`docs/DEPLOY.md`](docs/DEPLOY.md)
* **This file:** how it is built, how to run it locally, and how to change it.

| | |
|---|---|
| Analysis levels | L0 data check · L1 segmentation · L2 markets · L4 channel mix · L5 cohorts and trends. L3 product categories: planned. |
| Data source | BigQuery (Storage Read API, mapped columns only). CSV upload via Cloud Storage: planned. |
| Stack | Python 3.12 · pandas · FastAPI · vanilla JS + ECharts (no build step) · Cloud Run · Cloud Storage |
| Tests | `pytest -q` — 21 tests, including exact parity with the original Colab notebook |

---

## Architecture

```
 browser (analyst, behind IAP)                         browser (client, public link)
        │                                                        │
 ┌──────▼────────── Cloud Run: rfm-studio (APP_MODE=full) ──┐   ┌▼── Cloud Run: rfm-share (APP_MODE=share) ┐
 │ app/main.py      HTTP: pages, JSON API, downloads        │   │ app/main.py  GET /r/<token> only           │
 │ app/runs.py      runs, background jobs, sharing          │   └────────────────────┬──────────────────────┘
 │ app/sources.py   BigQuery (and local CSV folders in dev) │                        │ read-only
 │ app/storage.py   Cloud Storage / local folder            │                        │
 │ rfm_engine/      the analysis (pure Python + pandas)     │                        │
 └──────┬───────────────────────────────┬───────────────────┘                        │
        │ Storage Read API              │ read / write                               │
   BigQuery (client tables)     Cloud Storage bucket: runs/…, shares/… ◄─────────────┘
```

* **One image, two services.** `APP_MODE=share` registers only `/r/{token}` and `/healthz`; that service has a
  read-only service account and no BigQuery access.
* **Jobs run in a thread pool** inside the single app instance (`RFM_WORKERS`, default 2). Status and progress are
  written to the bucket, so the page survives reloads. Hence Cloud Run must run with max instances 1 and
  instance-based billing (CPU stays on after the request returns).
* **The report is data, not HTML.** `run_report()` returns one JSON document with every filter combination
  precomputed. The same JS (`core.js`, `charts.js`, `report.js`) renders it in the app and in the self-contained
  shared page, which inlines the CSS, ECharts, the JS and the JSON into one file (`app/report_html.py`).

## Repository layout

```
rfm_engine/            analysis engine — no web or cloud imports
  spec.py              field spec, default segment rules, segment descriptions
  config.py            RunConfig: every setting of a run + validation; safe segment-rule checker
  load.py              normalise raw tables, suggest the column mapping
  quality.py           L0: field mapping, cleaning, FX, trims, identity linking, items check (DQLog)
  customers.py         one row per customer: R/F/M inputs, enrichment, outliers, market/channel/loyalty, acquisition
  scoring.py           L1: cut-offs, scores, segment rules, segment KPIs
  cohorts.py           L5: retention, revenue per customer, time to 2nd purchase, monthly development
  views.py             report data per filter combination; L2 markets; generated insights
  pipeline.py          run_check() / run_report() entry points, exports
app/
  main.py              FastAPI app factory, routes, security headers
  runs.py              run store (meta/config/status files), job runner, cache of source tables, sharing
  sources.py           BigQuery listing / inspection / loading; local CSV folders for development
  storage.py           Storage interface: GCSStorage, LocalStorage (key whitelist, atomic local writes)
  report_html.py       builds the self-contained report page
  settings.py          environment variables
  web/index.html       app shell
  web/report.html      template of the shared report page
  web/static/js/core.js     formatting, brand tokens, chart theme, small UI helpers
  web/static/js/charts.js   report card registry (RFM.CARDS) and tab layout (RFM.LAYOUT)
  web/static/js/report.js   report renderer: filters, tabs, cards, table view, explanations
  web/static/js/app.js      the app: runs list, Connect, Settings, Data check, Report
  web/static/css/rfm.css    Precis brand styles (Space Grotesk + Inter)
  web/static/vendor/        ECharts 5.5.1 (served by the app; no CDN at runtime)
tests/                 engine tests, API tests, notebook reference numbers
sample_data/           dummy client tables used by tests and local development
notebooks/             the original Colab notebooks
prototype/             the approved clickable design prototype (example data)
scripts/run_engine.py  run the engine on a folder of CSVs from the command line
docs/                  user guide, deployment guide
Dockerfile             production image (uvicorn, one worker, non-root)
```

## Running locally

```bash
python -m venv .venv
```

```bash
.venv/bin/pip install -r requirements-dev.txt
```

```bash
RFM_STORAGE=./data RFM_LOCAL_DATA_DIR=. .venv/bin/uvicorn app.main:app --reload
```

Open http://localhost:8000. `RFM_LOCAL_DATA_DIR=.` offers every sub-folder with CSVs (here `sample_data`) as a
**Sample data** source on the Connect step, so the whole flow works without Google Cloud. Runs are stored in
`./data` (git-ignored). To use real BigQuery locally, log in with `gcloud auth application-default login` and set
`RFM_BQ_PROJECT`.

Engine only, from the command line:

```bash
.venv/bin/python scripts/run_engine.py sample_data --snapshot 2026-11-01 --client "Dummy Nordic Retail" --out out/
```

Tests:

```bash
.venv/bin/pytest -q
```

## The engine

```python
from rfm_engine import RunConfig, run_check, run_report

cfg = RunConfig(client_name="Client", snapshot_date="2026-11-01", reporting_currency="SEK")
check = run_check(tables, cfg)      # data check: verdict, findings, cleaning log, descriptive stats, trim options
result = run_report(tables, cfg)    # result.report (JSON for the page), result.exports (file name -> bytes)
```

`tables` maps `transactions` / `customers` / `items` / `fx_rates` to DataFrames as read (any dtype; everything is
normalised to text first). Client→spec column renames come from `cfg.column_map`.

Blocking problems raise `RFMStop(title, what, fix)`; the API returns it as HTTP 422 and the UI shows it as a red box.
Non-blocking findings are logged with `DQLog.issue(table, check, severity, rows, total, action)`.

**Pipeline order:** normalise → field mapping → dedupe → dates and window → amounts → transaction types →
hashed emails → currencies and FX → **trims** → identity linking → items check → customer metrics and outliers →
scores → segments → views per filter → exports.

**Parity with the notebook.** `tests/reference/notebook_reference.json` holds the numbers the original notebook
produces on `sample_data` (row counts per cleaning step, findings, cut-offs, segment sizes, KPIs). The engine must
reproduce them exactly. Regenerate after an intended logic change:

```bash
.venv/bin/python tests/reference/run_notebook.py notebooks/RFM_Descriptive_Cohorts.ipynb
```

**Report JSON (shape):**

```
meta            client, snapshot, window, currency, engine version, what data is present (has.market, …)
filters         options for market / channel / loyalty with customer counts
segments        order, rule definitions, empty segments
thresholds, threshold_table
data            verdict, counts, stats, coverage, issues, cleaning_log, trim_log      (from the data check)
views           "<market>|<channel>|<loyalty>" → { customers, overview, segments, cohorts }
markets         "<channel>|<loyalty>" → { rows, average, segment_share, retention, small_segments }
```

Filters only subset already-scored customers; they never re-score.

## Changing things

| I want to… | Do this |
|---|---|
| Change a default (bins, rules, outlier quantile…) | `rfm_engine/config.py` (`RunConfig`) or `spec.py` (`DEFAULT_RULES`, `SEGMENT_INFO`) |
| Add a field to the spec | `rfm_engine/spec.py` (`SPEC`, and `SYNONYMS` for auto-mapping) |
| Add a data-quality check | a `dq.issue(...)` call in `quality.py` (or `customers.py`); it appears in the findings automatically |
| Add a setting | field on `RunConfig` + validation in `validate()`, use it in the engine, add the input in `app.js → settings()` |
| Add a metric to a tab | compute it in `views.py` (`overview_section`, `segments_section`, `markets_section`) or `cohorts.py` |
| Add a chart or card | one entry in `RFM.CARDS` (`charts.js`) + its id in `RFM.LAYOUT`. Template at the top of the file. Filters, hover, "How to read", "View as table" and the shared page come for free. |
| Add a report tab | a key in `RFM.LAYOUT` with a label and rows |
| Change colours or type | `rfm.css` (`:root` tokens) and `core.js` (`RFM.T`, `RFM.SERIES`, `RFM.SEGMENT_COLORS`) |

Chart colours are validated for colour-vision deficiency: categorical series use `RFM.SERIES` in fixed order (max 4),
segments use `RFM.SEGMENT_COLORS` (max 8, fixed by rule order). Don't cycle colours; group the rest into "Other".

After any change: `pytest -q`, then click through the four steps locally on the sample data.

## HTTP API

All under the app service (behind IAP). Errors: `{"error": {"title", "what", "fix"}}`.

| Method | Path | |
|---|---|---|
| GET | `/api/spec` | Field spec, defaults, rule variables, local sample folders |
| GET / POST | `/api/runs` | List runs · create (`{client}` or `{clone_from}`) |
| GET / DELETE | `/api/runs/{id}` | Meta, config, job status, files, `stale` flags · delete |
| PUT | `/api/runs/{id}/config` | Validate and save settings (422 with a friendly error) |
| POST | `/api/source/tables` | List tables in a BigQuery dataset |
| PUT | `/api/runs/{id}/source` | Assign tables; returns columns, samples, suggested mapping |
| POST | `/api/runs/{id}/check` · `/report` | Start a background job (409 if one is running) |
| GET | `/api/runs/{id}/status` | Job state, progress, error |
| GET | `/api/runs/{id}/check` · `/report` · `/report.html` | Results |
| GET | `/api/runs/{id}/files/{name}` · `/download.zip` | Downloads |
| GET | `/api/baselines` | Runs with a report (for frozen cut-offs) |
| POST / DELETE | `/api/runs/{id}/share` · `/share/{token}` | Create / remove a share link |
| GET | `/r/{token}` | Shared report (both services) |
| GET | `/healthz` | Health check |

Interactive docs at `/api/docs` on the app service.

## Storage layout (bucket or local folder)

```
runs/<id>/meta.json        client, source, timestamps, last check/report summary and settings hash
runs/<id>/config.json      RunConfig + baseline_run_id
runs/<id>/status.json      current/last job: state, progress, error, heartbeat
runs/<id>/input/*.parquet  cached source tables + fingerprint.json (re-read when BigQuery tables change)
runs/<id>/check.json       data-check result
runs/<id>/report.json      report data
runs/<id>/report.html      self-contained report page
runs/<id>/files/*          downloads
shares/<token>.json|.html  share link (run, expiry) and the frozen page
```

## Security

* App behind Identity-Aware Proxy; share service public but only serves unguessable (192-bit), expiring,
  revocable links with `noindex`.
* Strict CSP: the app runs only its own scripts; the shared page cannot load or send anything.
* Segment rules are evaluated with `DataFrame.eval` only after a token whitelist (`config.check_rule`): variables,
  numbers, comparisons, `and`/`or`/`not` — nothing else.
* Storage keys and run IDs are validated against strict patterns; downloads are whitelisted by name.
* BigQuery access is read-only and column-limited; hashed emails only leave the bucket in `customer_segments.csv`.
* Container runs as a non-root user.

## Known limits and next steps

* **One app instance** by design (in-process jobs). For heavy concurrent use, move jobs to Cloud Run Jobs or Cloud Tasks.
* **Speed:** ~10 s for 100k transactions (24 months, 5 markets, 72 filter views). Very large clients mainly add
  filter views × cohort work; first optimisation would be pre-aggregating per customer-month.
* **Planned:** L3 product categories tab and category filter; CSV upload through Cloud Storage (signed URLs) for
  files over 30 MB; segment movement between two runs (L5 with frozen cut-offs).
