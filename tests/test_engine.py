"""Engine tests.

1. Reproduces the original Colab notebook exactly on sample_data (tests/reference/notebook_reference.json,
   produced by tests/reference/run_notebook.py).
2. Small hand-made datasets where the right answer can be worked out by hand.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from rfm_engine import RFMStop, RunConfig, run_check, run_report, suggest_mapping
from rfm_engine.cohorts import cohort_tables
from rfm_engine.config import check_rule

ROOT = Path(__file__).resolve().parents[1]
REF = json.loads((ROOT / "tests/reference/notebook_reference.json").read_text())


@pytest.fixture(scope="module")
def tables():
    return {t: pd.read_csv(ROOT / f"sample_data/{t}.csv", dtype=str) for t in ("transactions", "customers", "items", "fx_rates")}


@pytest.fixture(scope="module")
def result(tables):
    return run_report(tables, RunConfig(client_name="Dummy Nordic Retail", snapshot_date=REF["snapshot"]))


# ---------------- 1 · parity with the notebook ----------------
def test_window_and_row_counts(result):
    d = result.report["data"]["stats"]
    assert result.report["meta"]["window_start"] == REF["window_start"]
    assert d["rows_received"] == REF["rows_received"]
    assert d["rows_clean"] == REF["rows_clean"]
    assert d["rows_identified"] == REF["rows_identified"]
    assert d["customers"] == REF["customers"]
    assert d["outliers"] == REF["outliers"]


def test_thresholds_match(result):
    for k, v in REF["thresholds"].items():
        assert result.report["thresholds"][k] == pytest.approx(v)


def test_segments_match(result):
    kpis = {r["segment"]: r for r in result.report["views"]["all|all|all"]["segments"]["kpis"]}
    sizes = {k: v["customers"] for k, v in kpis.items() if k != "Total"}
    assert sizes == REF["segment_sizes"]
    assert kpis["Total"]["customers"] == REF["scored"]
    assert kpis["Total"]["net_revenue"] == pytest.approx(REF["net_revenue"], rel=1e-6)
    for k in ("aov", "avg_purchases", "median_days_since_purchase", "return_rate", "revenue_per_customer"):
        assert kpis["Total"][k] == pytest.approx(REF["kpi_total"][k], rel=1e-4), k


def test_coverage_and_dq_match(result):
    for k, v in REF["coverage"].items():
        assert result.report["data"]["coverage"][k] == pytest.approx(v, abs=1e-5)
    ours = sorted([[r["table"], r["check"], r["severity"], r["rows"]] for r in result.report["data"]["issues"]])
    assert ours == REF["dq"]
    log = [[r["step"], r["rows_after"]] for r in result.report["data"]["cleaning_log"]]
    assert log == REF["cleaning_log"]


# ---------------- 2 · report structure ----------------
def test_views_and_filters(result):
    rep = result.report
    assert "all|all|all" in rep["views"]
    markets = [o["value"] for o in rep["filters"]["market"]]
    assert markets[:2] == ["SE", "NO"]
    # every market view adds up to the total
    total = rep["views"]["all|all|all"]["customers"]
    assert sum(rep["views"][f"{m}|all|all"]["customers"] for m in markets) == total
    mk = rep["markets"]["all|all"]
    assert sum(r["customers"] for r in mk["rows"]) == total
    assert json.dumps(rep)  # JSON-safe


def test_exports(result):
    ex = result.exports
    for f in ("customer_segments.csv", "segment_kpis.csv", "l0_dq_report.csv", "rfm_thresholds.json", "cohort_retention.csv"):
        assert f in ex and len(ex[f]) > 10
    cs = pd.read_csv(pd.io.common.BytesIO(ex["customer_segments.csv"]))
    assert len(cs) == REF["customers"]          # scored + excluded outliers


def test_cohort_sanity(result):
    coh = result.report["views"]["all|all|all"]["cohorts"]
    assert coh["acquired"] > 0
    for row in coh["rows"]:
        for v in row["retention"]:
            assert v is None or 0 <= v <= 1
    shares = [p["share"] for p in coh["second_purchase"]]
    assert shares == sorted(shares)             # cumulative curve never falls


# ---------------- 3 · hand-made data ----------------
H = "a" * 64


def _h(i: int) -> str:
    return f"{i:064x}"


def toy(rows, customers=None):
    cols = ["transaction_id", "transaction_date", "hashed_email", "customer_id", "transaction_amount", "currency",
            "transaction_type", "market", "medium"]
    t = {"transactions": pd.DataFrame(rows, columns=cols).astype(str).replace("None", None)}
    if customers is not None:
        t["customers"] = pd.DataFrame(customers, columns=["hashed_email", "customer_id", "first_purchase_date", "loyalty_member", "marketing_consent"]).astype(str).replace("None", None)
    return t


def test_cohort_retention_by_hand():
    # customer 1: Jan, Feb, Apr · customer 2: Jan only · customer 3: Feb, Mar
    rows = [
        ("t1", "2026-01-10", _h(1), None, 100, "SEK", "purchase", "SE", "ecommerce"),
        ("t2", "2026-02-10", _h(1), None, 100, "SEK", "purchase", "SE", "ecommerce"),
        ("t3", "2026-04-10", _h(1), None, 100, "SEK", "purchase", "SE", "ecommerce"),
        ("t4", "2026-01-15", _h(2), None, 50, "SEK", "purchase", "SE", "retail"),
        ("t5", "2026-02-20", _h(3), None, 70, "SEK", "purchase", "SE", "ecommerce"),
        ("t6", "2026-03-20", _h(3), None, 70, "SEK", "purchase", "SE", "ecommerce"),
    ]
    cfg = RunConfig(snapshot_date="2026-05-01", lookback_months=12, cohort_start="first_in_window",
                    segment_rules=[["All", "R >= 1"]], cohort_periods=3)
    r = run_report(toy(rows), cfg).report
    coh = {row["cohort"]: row for row in r["views"]["all|all|all"]["cohorts"]["rows"]}
    assert coh["2026-01"]["customers"] == 2
    assert coh["2026-01"]["retention"] == [0.5, 0.0, 0.5]       # Feb: c1 · Mar: none · Apr: c1
    assert coh["2026-02"]["retention"] == [1.0, 0.0, None]      # Mar: c3 · Apr: none · May not complete
    assert coh["2026-01"]["cum_revenue_per_customer"][0] == pytest.approx(75.0)


def test_first_ever_cohort_excludes_returning_customers():
    rows = [("t1", "2026-03-01", _h(1), None, 100, "SEK", "purchase", "SE", "ecommerce"),
            ("t2", "2026-03-01", _h(2), None, 100, "SEK", "purchase", "SE", "ecommerce")]
    customers = [(_h(1), None, "2020-01-01", "true", "true"), (_h(2), None, "2026-03-01", "false", "false")]
    cfg = RunConfig(snapshot_date="2026-05-01", lookback_months=12, segment_rules=[["All", "R >= 1"]])
    coh = run_report(toy(rows, customers), cfg).report["views"]["all|all|all"]["cohorts"]
    assert coh["acquired"] == 1                                 # customer 1 bought before the window
    cfg.cohort_start = "first_in_window"
    coh = run_report(toy(rows, customers), cfg).report["views"]["all|all|all"]["cohorts"]
    assert coh["acquired"] == 2


def test_trims_are_applied_and_logged():
    rows = [(f"t{i}", "2026-03-01", _h(i), None, 100 * i, "SEK", "purchase", m, "ecommerce")
            for i, m in zip(range(1, 7), ["SE", "SE", "NO", "NO", "DE", "SE"])]
    cfg = RunConfig(snapshot_date="2026-05-01", lookback_months=12,
                    trims={"exclude_markets": ["DE"], "max_amount": 450})
    chk = run_check(toy(rows), cfg)
    assert chk["stats"]["customers"] == 4                       # t5 (DE) and t6 (600 > 450) removed
    assert [t["removed"] for t in chk["trim_log"]] == [1, 1]
    assert {m["value"] for m in chk["trim_options"]["markets"]} == {"SE", "NO", "DE"}


def test_unsafe_rules_are_rejected():
    for bad in ("__import__('os')", "R.__class__", "@x > 1", "R >= 4; F", "'a' == 'a'"):
        with pytest.raises(RFMStop):
            check_rule("x", bad)
    check_rule("ok", "R >= 4 and not has_prior_history or (F == 1 and monetary > 100.5)")


def test_config_validation():
    with pytest.raises(RFMStop):
        RunConfig(frequency_bins=[1, 2, 2, 4, 6]).validate()
    with pytest.raises(RFMStop):
        RunConfig(reporting_currency="KRONA").validate()
    c = RunConfig(frequency_bins="1, 2, 3, 5, 8", column_map={"transactions": {"OrderID": "transaction_id", "Foo": "bar"}})
    w = c.validate()
    assert c.frequency_bins == [1, 2, 3, 5, 8]
    assert c.column_map["transactions"] == {"orderid": "transaction_id"} and len(w) == 1


def test_missing_required_column_stops():
    t = toy([("t1", "2026-03-01", _h(1), None, 100, "SEK", "purchase", "SE", "ecommerce")])
    t["transactions"] = t["transactions"].drop(columns=["transaction_amount"])
    with pytest.raises(RFMStop) as e:
        run_check(t, RunConfig(snapshot_date="2026-05-01"))
    assert "transaction_amount" in e.value.what


def test_suggest_mapping():
    m = suggest_mapping(["OrderID", "Order Date", "Email_SHA256", "TotalExTax", "Country"], "transactions")
    assert m["transaction_id"] == "OrderID" and m["transaction_date"] == "Order Date"
    assert m["hashed_email"] == "Email_SHA256" and m["market"] == "Country"
    assert m["transaction_amount"] is None or m["transaction_amount"] == "TotalExTax"
