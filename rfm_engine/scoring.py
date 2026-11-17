"""L1 — R, F, M scores, segment rules and segment KPIs (notebook 4.1–4.3).

Cut-offs are calculated once on the whole scored population (or frozen from a baseline run);
report filters only subset customers, they never re-score them.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .config import RunConfig
from .spec import SEGMENT_INFO


def thresholds(scored: pd.DataFrame, cfg: RunConfig, snap: pd.Timestamp) -> dict[str, Any]:
    if cfg.frozen_thresholds:
        th = dict(cfg.frozen_thresholds)
        th["frozen"] = True
        return th
    return {
        "recency_days_edges": scored.recency_days.quantile([0.2, 0.4, 0.6, 0.8]).round(0).tolist(),
        "monetary_edges": scored.monetary.quantile([0.2, 0.4, 0.6, 0.8]).round(2).tolist(),
        "frequency_bin_edges": list(cfg.frequency_bins),
        "reporting_currency": cfg.reporting_currency, "snapshot_date": str(snap.date()),
        "lookback_months": cfg.lookback_months, "frozen": False,
    }


def score(cust: pd.DataFrame, cfg: RunConfig, snap: pd.Timestamp) -> tuple[pd.DataFrame, dict[str, Any]]:
    scored = cust[~cust.is_outlier].copy() if cfg.outlier_action == "exclude" else cust.copy()
    th = thresholds(scored, cfg, snap)
    # R: fewer days = higher score; ties fall into the more recent bucket
    scored["R"] = 5 - np.searchsorted(th["recency_days_edges"], scored.recency_days, side="left")
    scored["F"] = np.searchsorted(th["frequency_bin_edges"], scored.frequency, side="right")
    scored["M"] = 1 + np.searchsorted(th["monetary_edges"], scored.monetary, side="right")
    scored[["R", "F", "M"]] = scored[["R", "F", "M"]].clip(1, 5).astype(int)
    return scored, th


def assign_segments(scored: pd.DataFrame, rules: list[list[str]]) -> tuple[pd.DataFrame, list[str]]:
    scored = scored.copy()
    scored["segment"] = None
    for name, rule in rules:
        free = scored.segment.isna()
        if not free.any():
            break
        hit = scored[free].eval(rule)
        scored.loc[hit[hit].index, "segment"] = name
    unassigned = int(scored.segment.isna().sum())
    if unassigned:
        scored["segment"] = scored.segment.fillna("Unassigned")
    present = set(scored.segment.unique())
    order = [s for s, _ in rules if s in present] + (["Unassigned"] if unassigned else [])
    return scored, order


def threshold_table(th: dict[str, Any], currency: str) -> list[dict[str, Any]]:
    re_, me, fe = th["recency_days_edges"], th["monetary_edges"], th["frequency_bin_edges"]
    f_labels = [f"{a}" if b - a == 1 else f"{a}–{b - 1}" for a, b in zip(fe, fe[1:])] + [f"{fe[-1]}+"]
    r_labels = [f"≤ {re_[0]:.0f}", f"{re_[0]:.0f}–{re_[1]:.0f}", f"{re_[1]:.0f}–{re_[2]:.0f}", f"{re_[2]:.0f}–{re_[3]:.0f}", f"> {re_[3]:.0f}"]
    m_labels = [f"> {me[3]:,.0f}", f"{me[2]:,.0f}–{me[3]:,.0f}", f"{me[1]:,.0f}–{me[2]:,.0f}", f"{me[0]:,.0f}–{me[1]:,.0f}", f"≤ {me[0]:,.0f}"]
    return [{"score": 5 - i, "recency_days": r_labels[i], "purchases": f_labels[::-1][i], f"net_revenue": m_labels[i]}
            for i in range(5)]


def segment_definitions(rules: list[list[str]], sizes: pd.Series) -> list[dict[str, Any]]:
    return [dict(order=i + 1, segment=s, rule=rule, customers=int(sizes.get(s, 0)),
                 description=SEGMENT_INFO.get(s, ("", ""))[0], suggested_action=SEGMENT_INFO.get(s, ("", ""))[1])
            for i, (s, rule) in enumerate(rules)]


def segment_kpis(scored: pd.DataFrame, order: list[str]) -> pd.DataFrame:
    """One row per segment plus a Total row. Works on any subset of scored customers."""
    tot_c, tot_m = len(scored), scored.monetary.sum()
    g = scored.groupby("segment")
    kpi = pd.DataFrame({
        "customers": g.size(),
        "customer_share": g.size() / tot_c if tot_c else np.nan,
        "net_revenue": g.monetary.sum(),
        "revenue_share": g.monetary.sum() / tot_m if tot_m else np.nan,
        "revenue_per_customer": g.monetary.mean(),
        "aov": g.gross_revenue.sum() / g.frequency.sum(),
        "avg_purchases": g.frequency.mean(),
        "median_days_since_purchase": g.recency_days.median(),
        "return_rate": -g.returns.sum() / g.gross_revenue.sum().replace(0, np.nan),
        "repeat_rate": g.frequency.apply(lambda s: (s >= 2).mean()),
    }).reindex(order)
    has_loy = scored.loyalty_member.notna().any()
    has_con = scored.marketing_consent.notna().any()
    if has_loy:
        kpi["loyalty_share"] = g.loyalty_member.apply(lambda s: s.fillna(False).astype(bool).mean()).reindex(order)
    if has_con:
        kpi["consent_share"] = g.marketing_consent.apply(lambda s: s.fillna(False).astype(bool).mean()).reindex(order)
    total = pd.DataFrame({
        "customers": tot_c, "customer_share": 1.0, "net_revenue": tot_m, "revenue_share": 1.0,
        "revenue_per_customer": scored.monetary.mean(),
        "aov": scored.gross_revenue.sum() / scored.frequency.sum() if tot_c else np.nan,
        "avg_purchases": scored.frequency.mean(),
        "median_days_since_purchase": scored.recency_days.median(),
        "return_rate": -scored.returns.sum() / scored.gross_revenue.sum() if scored.gross_revenue.sum() else np.nan,
        "repeat_rate": (scored.frequency >= 2).mean() if tot_c else np.nan}, index=["Total"])
    if has_loy:
        total["loyalty_share"] = scored.loyalty_member.fillna(False).astype(bool).mean()
    if has_con:
        total["consent_share"] = scored.marketing_consent.fillna(False).astype(bool).mean()
    kpi["customers"] = kpi.customers.fillna(0)
    out = pd.concat([kpi, total])
    out.index.name = "segment"
    return out
