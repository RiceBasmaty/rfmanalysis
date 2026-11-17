"""Report views: the data behind each report tab, for every filter combination.

Filters (market, channel, loyalty) select a subset of *scored* customers — scores and segments
never change with a filter. Each combination is precomputed so the report filters instantly in the
browser, also in the static shared HTML.

To add a visualisation: compute its data in one of the section builders below (or a new one),
then register the chart in the front-end chart registry under the same key.
"""
from __future__ import annotations

import html
import itertools
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .cohorts import cohort_tables, monthly_development
from .config import RunConfig
from .scoring import segment_kpis
from .util import money, records

CHANNEL_LABELS = {"online": "Ecommerce only", "store": "Retail only", "both": "Omnichannel"}
LOYALTY_LABELS = {"member": "Loyalty members", "non-member": "Non-members"}


@dataclass
class Context:
    cfg: RunConfig
    scored: pd.DataFrame
    txi: pd.DataFrame            # identified transactions of scored customers
    order: list[str]
    th: dict[str, Any]
    snap: pd.Timestamp
    wstart: pd.Timestamp
    has_market: bool
    has_medium: bool
    has_loyalty: bool
    n_outliers: int
    outlier_revenue: float
    coverage: dict[str, float]
    n_critical: int


def filter_options(ctx: Context) -> dict[str, list[dict[str, str]]]:
    sc = ctx.scored
    opts: dict[str, list[dict[str, str]]] = {"market": [], "channel": [], "loyalty": []}
    if ctx.has_market:
        for m, n in sc.market.value_counts().items():
            opts["market"].append({"value": str(m), "label": str(m), "customers": int(n)})
    if ctx.has_medium:
        for c in ("online", "store", "both"):
            n = int((sc.channel == c).sum())
            if n:
                opts["channel"].append({"value": c, "label": CHANNEL_LABELS[c], "customers": n})
    if ctx.has_loyalty:
        for c in ("member", "non-member"):
            n = int((sc.loyalty == c).sum())
            if n:
                opts["loyalty"].append({"value": c, "label": LOYALTY_LABELS[c], "customers": n})
    return opts


def view_key(market: str = "all", channel: str = "all", loyalty: str = "all") -> str:
    return f"{market}|{channel}|{loyalty}"


def build_all_views(ctx: Context) -> tuple[dict[str, Any], dict[str, Any]]:
    """Returns (views keyed market|channel|loyalty, markets sections keyed channel|loyalty)."""
    opts = filter_options(ctx)
    mk = ["all"] + [o["value"] for o in opts["market"]] if len(opts["market"]) > 1 else ["all"]
    ch = ["all"] + [o["value"] for o in opts["channel"]] if len(opts["channel"]) > 1 else ["all"]
    ly = ["all"] + [o["value"] for o in opts["loyalty"]] if len(opts["loyalty"]) > 1 else ["all"]
    sc = ctx.scored
    views, markets = {}, {}
    for c, l in itertools.product(ch, ly):
        base = np.ones(len(sc), dtype=bool)
        if c != "all":
            base &= (sc.channel == c).to_numpy()
        if l != "all":
            base &= (sc.loyalty == l).to_numpy()
        if base.sum() and ctx.has_market:
            markets[f"{c}|{l}"] = markets_section(ctx, sc[base])
        for m in mk:
            mask = base & ((sc.market == m).to_numpy() if m != "all" else True)
            if mask.sum() == 0:
                continue
            views[view_key(m, c, l)] = build_view(ctx, sc[mask])
    return views, markets


def build_view(ctx: Context, sc: pd.DataFrame) -> dict[str, Any]:
    t = ctx.txi[ctx.txi.customer_key.isin(sc.index)]
    kpi = segment_kpis(sc, ctx.order)
    return {
        "customers": int(len(sc)),
        "overview": overview_section(ctx, sc, kpi),
        "segments": segments_section(ctx, sc, kpi),
        "cohorts": {**cohort_tables(sc, t, ctx.cfg, ctx.snap, ctx.wstart),
                    "monthly": monthly_development(sc, t, ctx.snap, ctx.wstart)},
    }


# ---------------- Overview ----------------
def concentration(sc: pd.DataFrame, points: int = 100) -> dict[str, Any]:
    srt = sc.monetary.clip(lower=0).sort_values(ascending=False).to_numpy()
    if not len(srt) or srt.sum() <= 0:
        return {"curve": [], "top20": None, "half": None}
    cum = np.cumsum(srt) / srt.sum()
    n = len(srt)
    xs = np.linspace(0, 1, points + 1)
    ys = [0.0] + [float(cum[max(int(np.ceil(n * x)) - 1, 0)]) for x in xs[1:]]
    return {"curve": [[round(float(x), 4), round(y, 4)] for x, y in zip(xs, ys)],
            "top20": float(cum[max(int(np.ceil(n * 0.2)) - 1, 0)]),
            "half": float((np.searchsorted(cum, 0.5) + 1) / n)}


def overview_section(ctx: Context, sc: pd.DataFrame, kpi: pd.DataFrame) -> dict[str, Any]:
    tot = kpi.loc["Total"]
    conc = concentration(sc)
    head = {
        "customers": int(len(sc)), "net_revenue": float(sc.monetary.sum()),
        "revenue_per_customer": float(sc.monetary.mean()), "avg_purchases": float(sc.frequency.mean()),
        "repeat_rate": float((sc.frequency >= 2).mean()), "return_rate": float(tot.return_rate) if pd.notna(tot.return_rate) else None,
        "aov": float(tot.aov) if pd.notna(tot.aov) else None,
        "consent_share": float(tot.consent_share) if "consent_share" in tot and pd.notna(tot.consent_share) else None,
        "loyalty_share": float(tot.loyalty_share) if "loyalty_share" in tot and pd.notna(tot.loyalty_share) else None,
        "median_days_since_purchase": float(sc.recency_days.median()),
    }
    return {"headline": head, "concentration": conc, "insights": insights(ctx, sc, kpi, conc)}


# ---------------- Segments ----------------
def segments_section(ctx: Context, sc: pd.DataFrame, kpi: pd.DataFrame) -> dict[str, Any]:
    g = sc.assign(R=sc.R.astype(int), F=sc.F.astype(int)).groupby(["F", "R"])
    rf = pd.DataFrame({"customers": g.size(), "revenue_per_customer": g.monetary.mean()}).reset_index()
    out: dict[str, Any] = {
        "kpis": records(kpi.reset_index()),
        "rf_grid": records(rf),
        "score_counts": {s: [int((sc[s] == v).sum()) for v in range(1, 6)] for s in "RFM"},
    }
    if ctx.has_medium:
        known = sc[sc.channel != "unknown"]
        mix = pd.crosstab(known.segment, known.channel, normalize="index").reindex(index=ctx.order, columns=["online", "store", "both"]).fillna(0)
        mix = mix[mix.index.isin(known.segment.unique())]
        out["channel_mix"] = [{"segment": s, "online": float(r.online), "store": float(r.store), "both": float(r.both)}
                              for s, r in mix.iterrows()]
    return out


# ---------------- Markets (L2) ----------------
def markets_section(ctx: Context, sc: pd.DataFrame) -> dict[str, Any]:
    cfg = ctx.cfg
    tot_m = sc.monetary.sum()
    avg = {"revenue_per_customer": float(sc.monetary.mean()), "repeat_rate": float((sc.frequency >= 2).mean()),
           "aov": float(sc.gross_revenue.sum() / sc.frequency.sum())}
    rows, seg_share, retention, small = [], [], [], []
    markets = sc.market.value_counts().index.tolist()
    for m in markets:
        s = sc[sc.market == m]
        gross = s.gross_revenue.sum()
        rows.append({
            "market": m, "customers": int(len(s)), "customer_share": float(len(s) / len(sc)),
            "net_revenue": float(s.monetary.sum()), "revenue_share": float(s.monetary.sum() / tot_m) if tot_m else None,
            "revenue_per_customer": float(s.monetary.mean()), "aov": float(gross / s.frequency.sum()),
            "avg_purchases": float(s.frequency.mean()), "repeat_rate": float((s.frequency >= 2).mean()),
            "return_rate": float(-s.returns.sum() / gross) if gross else None,
            "consent_share": float(s.marketing_consent.fillna(False).astype(bool).mean()) if s.marketing_consent.notna().any() else None,
            "median_days_since_purchase": float(s.recency_days.median()),
        })
        vc = s.segment.value_counts()
        for seg in ctx.order:
            n = int(vc.get(seg, 0))
            seg_share.append({"market": m, "segment": seg, "customers": n, "share": n / len(s)})
            if 0 < n < cfg.small_segment_threshold:
                small.append({"market": m, "segment": seg, "customers": n})
        t = ctx.txi[ctx.txi.customer_key.isin(s.index)]
        ct = cohort_tables(s, t, cfg, ctx.snap, ctx.wstart, max_rows=0, curve_only=True)
        retention.append({"market": m, "acquired": ct["acquired"], "curve": ct.get("retention_curve", [])})
    return {"rows": rows, "average": avg, "segment_share": seg_share, "retention": retention, "small_segments": small,
            "small_threshold": cfg.small_segment_threshold}


# ---------------- Insights (notebook 4.5) ----------------
def insights(ctx: Context, sc: pd.DataFrame, kpi: pd.DataFrame, conc: dict[str, Any]) -> list[str]:
    cur, cfg = ctx.cfg.reporting_currency, ctx.cfg
    e = html.escape
    k = kpi.drop(index="Total")
    k = k[k.customers > 0]

    def seg(name: str, col: str) -> float:
        return float(k.at[name, col]) if name in k.index else float("nan")

    tot_c, tot_m = len(sc), float(sc.monetary.sum())
    if not tot_c:
        return []
    out = [f"<b>{tot_c:,} customers</b> generated <b>{money(tot_m, cur)}</b> net revenue in the last {cfg.lookback_months} months; "
           f"on average {sc.frequency.mean():.1f} purchases and {money(sc.monetary.mean(), cur)} per customer."]
    if conc.get("top20") is not None:
        out.append(f"<b>Concentration:</b> the top 20% of customers account for <b>{conc['top20']:.0%}</b> of revenue.")
    out.append(f"<b>Repeat rate:</b> {(sc.frequency >= 2).mean():.0%} of customers bought more than once in the window.")
    if "Champions" in k.index:
        out.append(f"<b>Champions</b> are {seg('Champions', 'customer_share'):.0%} of customers but {seg('Champions', 'revenue_share'):.0%} "
                   f"of revenue, spending {seg('Champions', 'revenue_per_customer') / sc.monetary.mean():.1f}× the average customer.")
    hv = [s for s in ("Champions", "Loyal High Value") if s in k.index]
    if hv:
        out.append(f"<b>High-value core</b> ({' + '.join(hv)}): {k.loc[hv, 'customer_share'].sum():.0%} of customers, "
                   f"{k.loc[hv, 'revenue_share'].sum():.0%} of revenue.")
    if "At Risk High Value" in k.index:
        out.append(f"<b>At Risk High Value:</b> {seg('At Risk High Value', 'customers'):,.0f} customers who produced "
                   f"{money(seg('At Risk High Value', 'net_revenue'), cur)} ({seg('At Risk High Value', 'revenue_share'):.0%} of revenue) "
                   f"have not bought for a median of {seg('At Risk High Value', 'median_days_since_purchase'):.0f} days — the main win-back target.")
    if "New Customers" in k.index:
        out.append(f"<b>New Customers:</b> {seg('New Customers', 'customers'):,.0f} ({seg('New Customers', 'customer_share'):.0%}) made their "
                   f"first-ever purchase in the last ~{ctx.th['recency_days_edges'][1]:.0f} days; converting them to a second order "
                   f"is the cheapest growth lever.")
    if "Reactivated" in k.index:
        out.append(f"<b>Reactivated:</b> {seg('Reactivated', 'customers'):,.0f} recent single-order customers had bought before the window "
                   f"— without the customers table they would have been counted as new.")
    if "Hibernating" in k.index:
        out.append(f"<b>Hibernating:</b> {seg('Hibernating', 'customer_share'):.0%} of customers but only "
                   f"{seg('Hibernating', 'revenue_share'):.0%} of revenue; consider low-cost reactivation or suppression.")
    rr = k.return_rate.dropna()
    tr = kpi.at["Total", "return_rate"]
    if len(rr) and pd.notna(tr):
        out.append(f"<b>Returns:</b> overall return rate {tr:.1%}; highest in <b>{e(str(rr.idxmax()))}</b> ({rr.max():.1%}).")
    if sc.marketing_consent.notna().any():
        act = sc.marketing_consent.fillna(False).astype(bool)
        out.append(f"<b>Activation:</b> {act.mean():.0%} of scored customers have marketing consent "
                   f"({int(act.sum()):,} addressable via email / Customer Match / Meta).")
    if ctx.n_outliers:
        out.append(f"<b>Outliers:</b> {ctx.n_outliers} customer(s) flagged as outlier/B2B "
                   f"({'excluded' if cfg.outlier_action == 'exclude' else 'kept'}), {money(ctx.outlier_revenue, cur)} of revenue.")
    if "Retail purchases linked" in ctx.coverage:
        out.append(f"<b>Coverage caveat:</b> {ctx.coverage['Retail purchases linked']:.0%} of retail purchases are linked to a customer; "
                   f"retail behaviour of anonymous shoppers is not reflected.")
    if ctx.n_critical:
        out.append("<b>Data caveat:</b> the data check found critical issues — treat these numbers as indicative.")
    return out
