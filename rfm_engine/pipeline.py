"""Entry points used by the app (and by tests / the CLI).

    check = run_check(tables, config)      # step 03 · data check: L0 findings, descriptive stats, trim options
    result = run_report(tables, config)    # step 04 · the report data + exportable files

`tables` maps table name -> DataFrame as read from CSV / BigQuery. Column renames from the Connect
step live in config.column_map and are applied here.
"""
from __future__ import annotations

import io
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import numpy as np
import pandas as pd

from .config import RunConfig
from .customers import build_customers
from .load import normalise_table
from .quality import Prepared, prepare
from .scoring import assign_segments, score, segment_definitions, segment_kpis, threshold_table
from .spec import SPEC
from .util import jsonable, records
from .views import Context, build_all_views, filter_options

ENGINE_VERSION = "1.0.0"
Progress = Callable[[str, float], None]


def _noop(_label: str, _pct: float) -> None:
    pass


@dataclass
class ReportResult:
    report: dict[str, Any]          # JSON-safe: everything the report page needs
    exports: dict[str, bytes]       # file name -> content (CSV / JSON)
    seconds: float


def _prepare_all(tables: dict[str, pd.DataFrame], cfg: RunConfig, progress: Progress):
    warnings = cfg.validate()
    progress("Reading tables", 0.05)
    raw = {t: normalise_table(df, t, cfg.column_map.get(t)) for t, df in tables.items() if t in SPEC and df is not None}
    progress("Checking and cleaning the data", 0.15)
    prep = prepare(raw, cfg)
    progress("Building customer metrics", 0.35)
    cust, outliers = build_customers(prep)
    if cust.attrs.get("cohort_fallback"):
        warnings.append("Cohorts use the first purchase in the window: the customers table has no first_purchase_date, "
                        "so returning customers in early cohorts can look new.")
    elif cust.attrs.get("cohort_basis") == "first_in_window":
        warnings.append("Cohorts start at the first purchase in the window, so customers who bought before the window "
                        "appear as new in the earliest cohorts.")
    if cfg.trims.get("date_to"):
        warnings.append(f"Transactions after {cfg.trims['date_to']} are trimmed, but recency is still counted to the "
                        f"snapshot date {prep.snap.date()}.")
    return prep, cust, outliers, warnings


# ---------------- step 03 · data check ----------------
def run_check(tables: dict[str, pd.DataFrame], cfg: RunConfig, progress: Progress = _noop) -> dict[str, Any]:
    t0 = time.time()
    prep, cust, outliers, warnings = _prepare_all(tables, cfg, progress)
    progress("Summarising", 0.8)
    out = check_payload(prep, cust, outliers, warnings)
    out["seconds"] = round(time.time() - t0, 2)
    progress("Done", 1.0)
    return out


def _verdict(counts: dict[str, int]) -> dict[str, str]:
    if counts["critical"]:
        return {"level": "critical", "title": "Critical issues — results are indicative until the client fixes the export",
                "text": "See the critical rows below. Everything else ran, so you can still review the segments."}
    if counts["warning"]:
        return {"level": "warning", "title": f"Data is usable — {counts['warning']} issue{'s' if counts['warning'] != 1 else ''} fixed automatically",
                "text": "Review the warnings; mention the recurring ones to the client."}
    return {"level": "ok", "title": "Data is clean", "text": "No critical issues or warnings."}


def _nice_edges(median: float) -> list[float]:
    if not median or median <= 0 or not np.isfinite(median):
        return [0, 100, 250, 500, 1000, 2500, 5000]
    mag = 10 ** np.floor(np.log10(median / 4))
    base = [1, 2, 5]
    edges, v, i = [0.0], mag, 0
    while v < median * 16 and len(edges) < 9:
        edges.append(float(v))
        i += 1
        v = base[i % 3] * mag * 10 ** (i // 3)
    return edges


def check_payload(prep: Prepared, cust: pd.DataFrame, outliers: pd.DataFrame, warnings: list[str]) -> dict[str, Any]:
    cfg, tx, dq = prep.cfg, prep.tx, prep.dq
    counts = dq.counts()
    purch = tx[tx.transaction_type == "purchase"]
    ident = tx.customer_key.notna()
    monthly = tx.assign(ident=ident).groupby("month").agg(rows=("transaction_id", "size"), identified=("ident", "sum"),
                                                          net_revenue=("amount_rc", "sum")).reset_index()
    edges = _nice_edges(float(purch.amount_rc.median()) if len(purch) else 0)
    bins = pd.cut(purch.amount_rc, bins=edges + [np.inf], right=False, include_lowest=True)
    hist = bins.value_counts(sort=False)
    dist = [{"from": float(iv.left), "to": None if np.isinf(iv.right) else float(iv.right), "purchases": int(n),
             "share": float(n / len(purch)) if len(purch) else 0.0} for iv, n in hist.items()]
    below0 = int((purch.amount_rc < 0).sum())
    medium_cov = []
    if prep.has_medium:
        for m, g in purch.groupby("medium"):
            medium_cov.append({"medium": m, "purchases": int(len(g)), "identified": float(g.customer_key.notna().mean())})
    market_rows = []
    if prep.has_market:
        for m, g in tx.groupby(tx.market.astype("string").fillna("(empty)")):
            market_rows.append({"market": m, "rows": int(len(g)), "net_revenue": float(g.amount_rc.sum()),
                                "currency": ", ".join(sorted(g.currency.unique()))})
    rc = cfg.reporting_currency
    return jsonable({
        "engine_version": ENGINE_VERSION,
        "client": cfg.client_name, "currency": rc,
        "snapshot_date": prep.snap, "window_start": prep.wstart, "window_end": prep.snap - pd.Timedelta(days=1),
        "verdict": _verdict(counts), "counts": counts, "warnings": warnings,
        "stats": {"rows_received": prep.n_received, "rows_clean": len(tx), "rows_identified": len(prep.txi),
                  "customers": len(cust), "outliers": len(outliers),
                  "outlier_revenue_share": float(outliers.monetary.sum() / cust.monetary.sum()) if len(outliers) and cust.monetary.sum() else 0.0,
                  "with_prior_history": float(cust.has_prior_history.mean()) if prep.cu is not None else None,
                  "currencies": prep.currencies, "linked_rows": prep.linked_rows},
        "coverage": prep.coverage, "coverage_by_medium": medium_cov, "markets": market_rows,
        "loaded": prep.loaded, "mapping": records(prep.mapping), "extra_columns": prep.extras,
        "issues": records(dq.issues_df()), "cleaning_log": records(dq.steps_df()),
        "trim_options": prep.trim_options, "trims": cfg.trims, "trim_log": prep.trim_log,
        "items": prep.items,
        "monthly_volume": records(monthly), "order_value_distribution": dist, "purchases_below_zero": below0,
        "top_outliers": [{"customer": k[:12] + "…", "purchases": int(r.frequency), "net_revenue": float(r.monetary),
                          "aov": float(r.aov), "days_since_purchase": int(r.recency_days)}
                         for k, r in outliers.head(10).iterrows()],
    })


# ---------------- step 04 · report ----------------
def run_report(tables: dict[str, pd.DataFrame], cfg: RunConfig, progress: Progress = _noop) -> ReportResult:
    t0 = time.time()
    prep, cust, outliers, warnings = _prepare_all(tables, cfg, progress)
    progress("Scoring customers", 0.45)
    scored, th = score(cust, cfg, prep.snap)
    scored, order = assign_segments(scored, cfg.segment_rules)
    sizes = scored.segment.value_counts()
    seg_def = segment_definitions(cfg.segment_rules, sizes)
    unassigned = int(sizes.get("Unassigned", 0))
    if unassigned:
        warnings.append(f"{unassigned:,} customers matched no segment rule and are shown as Unassigned. "
                        f"Add a catch-all rule at the end (e.g. Other | R >= 1).")
    empty = [s for s, _ in cfg.segment_rules if s not in sizes.index]

    progress("Building the report views", 0.55)
    txi_sc = prep.txi[prep.txi.customer_key.isin(scored.index)]
    counts = prep.dq.counts()
    ctx = Context(cfg=cfg, scored=scored, txi=txi_sc, order=order, th=th, snap=prep.snap, wstart=prep.wstart,
                  has_market=prep.has_market, has_medium=prep.has_medium,
                  has_loyalty=bool(scored.loyalty_member.notna().any()),
                  n_outliers=int(cust.is_outlier.sum()), outlier_revenue=float(outliers.monetary.sum()),
                  coverage=prep.coverage, n_critical=counts["critical"])
    views, markets = build_all_views(ctx)

    progress("Preparing downloads", 0.9)
    check = check_payload(prep, cust, outliers, warnings)
    meta = {
        "client": cfg.client_name, "currency": cfg.reporting_currency,
        "snapshot_date": str(prep.snap.date()), "window_start": str(prep.wstart.date()),
        "window_end": str((prep.snap - pd.Timedelta(days=1)).date()), "lookback_months": cfg.lookback_months,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "engine_version": ENGINE_VERSION,
        "customers_scored": int(len(scored)), "outlier_action": cfg.outlier_action,
        "thresholds_frozen": bool(th.get("frozen")), "baseline_label": cfg.baseline_label,
        "cohort_basis": cust.attrs.get("cohort_basis"), "cohort_grain": cfg.cohort_grain, "cohort_periods": cfg.cohort_periods,
        "has": {"market": prep.has_market and len(scored.market.unique()) > 1, "medium": prep.has_medium,
                "loyalty": ctx.has_loyalty, "consent": bool(scored.marketing_consent.notna().any()),
                "items": prep.items is not None, "prior_history": prep.cu is not None},
    }
    report = jsonable({
        "meta": meta, "warnings": warnings, "filters": filter_options(ctx),
        "segments": {"order": order, "definitions": seg_def, "empty": empty, "unassigned": unassigned},
        "thresholds": {k: th.get(k) for k in ("recency_days_edges", "monetary_edges", "frequency_bin_edges")},
        "threshold_table": threshold_table(th, cfg.reporting_currency),
        "data": {k: check[k] for k in ("verdict", "counts", "stats", "coverage", "issues", "cleaning_log", "trim_log", "trims", "items")},
        "views": views, "markets": markets,
    }, digits=5)
    exports = build_exports(cfg, prep, cust, scored, outliers, th, seg_def, order, report, warnings)
    progress("Done", 1.0)
    return ReportResult(report=report, exports=exports, seconds=round(time.time() - t0, 2))


def _csv(df: pd.DataFrame) -> bytes:
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")


def build_exports(cfg: RunConfig, prep: Prepared, cust: pd.DataFrame, scored: pd.DataFrame, outliers: pd.DataFrame,
                  th: dict[str, Any], seg_def: list[dict[str, Any]], order: list[str], report: dict[str, Any],
                  warnings: list[str]) -> dict[str, bytes]:
    snap = str(prep.snap.date())
    cols = ["customer_key", "recency_days", "frequency", "monetary", "gross_revenue", "returns", "aov", "R", "F", "M",
            "segment", "first_purchase_date", "has_prior_history", "loyalty_member", "marketing_consent",
            "market", "channel", "acquisition_date"]
    out = scored.reset_index()[cols].copy()
    out["rfm_code"] = out.R.astype(str) + out.F.astype(str) + out.M.astype(str)
    out["snapshot_date"] = snap
    out["client"] = cfg.client_name
    if cfg.outlier_action == "exclude" and len(outliers):
        o = outliers.reset_index()[["customer_key", "recency_days", "frequency", "monetary", "gross_revenue", "returns", "aov", "market", "channel"]]
        o = o.assign(segment="Outlier (excluded)", snapshot_date=snap, client=cfg.client_name)
        out = pd.concat([out, o], ignore_index=True)
    kpis = segment_kpis(scored, order).reset_index()
    view = report["views"].get("all|all|all", {})
    coh = view.get("cohorts", {})
    coh_rows = [{"cohort": r["cohort"], "customers": r["customers"], **{f"p{k + 1}": v for k, v in enumerate(r["retention"])}}
                for r in coh.get("rows", [])]
    mk = report["markets"].get("all|all", {})
    meta = {**report["meta"], "config": cfg.to_dict(), "coverage": prep.coverage, "warnings": warnings,
            "segment_rules": cfg.segment_rules}
    files = {
        "customer_segments.csv": _csv(out),
        "segment_kpis.csv": _csv(kpis),
        "segment_definitions.csv": _csv(pd.DataFrame(seg_def)),
        "l0_dq_report.csv": _csv(prep.dq.issues_df()),
        "l0_cleaning_log.csv": _csv(prep.dq.steps_df()),
        "cohort_retention.csv": _csv(pd.DataFrame(coh_rows)),
        "monthly_development.csv": _csv(pd.DataFrame(coh.get("monthly", []))),
        "rfm_thresholds.json": json.dumps(jsonable({k: v for k, v in th.items() if k != "frozen"}), indent=2).encode(),
        "run_metadata.json": json.dumps(jsonable(meta), indent=2).encode(),
    }
    if mk:
        files["market_kpis.csv"] = _csv(pd.DataFrame(mk["rows"]))
    return files
