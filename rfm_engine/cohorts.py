"""L5 — cohorts and change over time.

A cohort is the group of customers acquired in the same month (or quarter). "Acquired" means the
customer's first-ever purchase (customers.first_purchase_date) falls inside the analysis window, or —
when cohort_start is first_in_window or there is no first_purchase_date — their first purchase in the
window. Only complete periods are used: the period that contains the snapshot date is left out.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .config import RunConfig

SECOND_PURCHASE_DAYS = (7, 14, 30, 60, 90, 180, 365)


def _period(d: pd.Series | pd.DatetimeIndex, grain: str) -> np.ndarray:
    d = pd.DatetimeIndex(d)
    return (d.year * 12 + d.month - 1).to_numpy() if grain == "month" else (d.year * 4 + (d.month - 1) // 3).to_numpy()


def _label(p: int, grain: str) -> str:
    return f"{p // 12}-{p % 12 + 1:02d}" if grain == "month" else f"{p // 4} Q{p % 4 + 1}"


def cohort_tables(cust: pd.DataFrame, txi: pd.DataFrame, cfg: RunConfig, snap: pd.Timestamp,
                  wstart: pd.Timestamp, max_rows: int = 24, curve_only: bool = False) -> dict[str, Any]:
    """cust: customer subset (all columns from build_customers); txi: identified transactions of those customers."""
    grain, n_per = cfg.cohort_grain, cfg.cohort_periods
    acq = cust[cust.acquired_in_window]
    last_complete = int(_period(pd.DatetimeIndex([snap]), grain)[0]) - 1
    out: dict[str, Any] = {"grain": grain, "periods": n_per, "acquired": int(len(acq)), "rows": [],
                           "basis": cust.attrs.get("cohort_basis", cfg.cohort_start)}
    if acq.empty:
        out.update(summary=_summary(acq, snap, None, n_per), second_purchase=[], ltv=[], retention_curve=[])
        return out

    cp = pd.Series(_period(acq.acquisition_date, grain), index=acq.index)
    t0 = txi[txi.customer_key.isin(acq.index)][["customer_key", "transaction_date", "transaction_type", "amount_rc"]]
    t = t0.assign(cohort=t0.customer_key.map(cp).to_numpy(), per=_period(t0.transaction_date, grain))
    t["offset"] = t.per - t.cohort
    t = t[(t.offset >= 0) & (t.per <= last_complete)]
    buys = t[t.transaction_type == "purchase"]

    sizes = cp.value_counts().sort_index()
    sizes = sizes[sizes.index <= last_complete]
    cohorts = sizes.index.to_numpy()
    offsets = np.arange(n_per + 1)
    active = (buys[buys.offset.between(1, n_per)].groupby(["cohort", "offset"]).customer_key.nunique()
              .unstack(fill_value=0).reindex(index=cohorts, columns=offsets, fill_value=0).to_numpy(dtype=float))
    revenue = (t[t.offset <= n_per].groupby(["cohort", "offset"]).amount_rc.sum()
               .unstack(fill_value=0.0).reindex(index=cohorts, columns=offsets, fill_value=0.0).to_numpy(dtype=float))
    size = sizes.to_numpy(dtype=float)[:, None]
    observed = offsets[None, :] <= (last_complete - cohorts)[:, None]          # cohort x offset
    ret = np.where(observed, active / size, np.nan)[:, 1:]
    base = revenue[:, :1]
    rev_ret = np.where(observed & (base > 0), revenue / np.where(base > 0, base, 1), np.nan)[:, 1:]
    cum = np.where(observed, np.cumsum(revenue, axis=1) / size, np.nan)

    def lst(a: np.ndarray) -> list[float | None]:
        return [None if np.isnan(v) else float(v) for v in a]

    rows = [{"cohort": _label(int(c), grain), "period": int(c), "customers": int(sizes.iloc[i]),
             "retention": lst(ret[i]), "revenue_retention": lst(rev_ret[i]), "cum_revenue_per_customer": lst(cum[i])}
            for i, c in enumerate(cohorts) if last_complete - c >= 1]     # at least one follow-up period
    out["retention_curve"] = _weighted_curve(rows, n_per)
    if curve_only:
        return out
    out["rows"] = rows[-max_rows:] if max_rows else []
    out["ltv"] = _ltv_quarters(acq, t0, snap)
    out["second_purchase"] = _second_purchase(acq, snap)
    out["summary"] = _summary(acq, snap, out["retention_curve"], n_per)
    return out


def _weighted_curve(rows: list[dict[str, Any]], n_per: int) -> list[float | None]:
    """Customer-weighted retention by period across all cohorts that can be observed at that period."""
    curve = []
    for k in range(n_per):
        num = sum(r["retention"][k] * r["customers"] for r in rows if r["retention"][k] is not None)
        den = sum(r["customers"] for r in rows if r["retention"][k] is not None)
        curve.append(num / den if den else None)
    return curve


def _ltv_quarters(acq: pd.DataFrame, t: pd.DataFrame, snap: pd.Timestamp, n_lines: int = 4, months: int = 12) -> list[dict[str, Any]]:
    """Cumulative net revenue per acquired customer by months since acquisition, for up to four quarterly cohorts."""
    mp = pd.Series(_period(acq.acquisition_date, "month"), index=acq.index)
    qp = pd.Series(_period(acq.acquisition_date, "quarter"), index=acq.index)
    last_m = int(_period(pd.DatetimeIndex([snap]), "month")[0]) - 1
    tm = t.assign(mo=_period(t.transaction_date, "month"))
    tm["k"] = tm.mo - tm.customer_key.map(mp).to_numpy()
    tm = tm[(tm.k >= 0) & (tm.mo <= last_m)]
    rev = tm.groupby([tm.customer_key.map(qp).to_numpy(), "k"]).amount_rc.sum()
    lines = []
    for q in sorted(qp.unique()):
        members = mp[qp == q]
        # a quarter is observable at k only for months whose customers have k complete months
        obs_max = last_m - int(members.max())
        if obs_max < 0:
            continue
        cum, running = [], 0.0
        for k in range(0, months + 1):
            if k > obs_max:
                cum.append(None); continue
            running += float(rev.get((q, k), 0.0))
            cum.append(running / len(members))
        lines.append({"cohort": _label(int(q), "quarter"), "customers": int(len(members)), "observed": int(obs_max), "values": cum})
    need = min(6, max((ln["observed"] for ln in lines), default=0))
    lines = [ln for ln in lines if ln["observed"] >= need]
    return lines[-n_lines:]


def _second_purchase(acq: pd.DataFrame, snap: pd.Timestamp) -> list[dict[str, Any]]:
    days = (acq.second_purchase_date - acq.acquisition_date).dt.days
    age = (snap - acq.acquisition_date).dt.days
    out = []
    for d in SECOND_PURCHASE_DAYS:
        eligible = age >= d
        n = int(eligible.sum())
        if n == 0:
            continue
        out.append({"days": d, "share": float((days[eligible] <= d).sum() / n), "eligible": n})
    return out


def _summary(acq: pd.DataFrame, snap: pd.Timestamp, curve: list[float | None] | None, n_per: int) -> dict[str, Any]:
    if acq.empty:
        return {"acquired": 0, "back_90": None, "retention_last": None, "retention_period": None, "median_days_to_second": None}
    days = (acq.second_purchase_date - acq.acquisition_date).dt.days
    age = (snap - acq.acquisition_date).dt.days
    elig = age >= 90
    target = min(12, n_per)
    ret_last = None
    if curve:
        for k in range(target - 1, -1, -1):
            if curve[k] is not None:
                ret_last, target = curve[k], k + 1
                break
    return {"acquired": int(len(acq)),
            "back_90": float((days[elig] <= 90).sum() / elig.sum()) if elig.any() else None,
            "retention_last": ret_last, "retention_period": target,
            "median_days_to_second": float(days.median()) if days.notna().any() else None}


def monthly_development(cust: pd.DataFrame, txi: pd.DataFrame, snap: pd.Timestamp, wstart: pd.Timestamp) -> list[dict[str, Any]]:
    """Per calendar month: active, new and returning customers, net revenue split, orders and AOV."""
    if cust.empty:
        return []
    t = txi[["customer_key", "transaction_date", "transaction_type", "amount_rc", "transaction_id"]]
    acq_m = pd.Series(_period(cust.acquisition_date, "month"), index=cust.index).where(cust.acquired_in_window)
    t = t.assign(mo=_period(t.transaction_date, "month"))
    t["is_new"] = (t.customer_key.map(acq_m).to_numpy() == t.mo)
    buys = t[t.transaction_type == "purchase"]
    g_all = t.groupby(["mo", "is_new"]).amount_rc.sum().unstack(fill_value=0.0)
    g_cust = buys.groupby(["mo", "is_new"]).customer_key.nunique().unstack(fill_value=0)
    orders = buys.groupby("mo").transaction_id.nunique()
    gross = buys.groupby("mo").amount_rc.sum()
    last_full = int(_period(pd.DatetimeIndex([snap]), "month")[0]) - 1
    out = []
    for m in range(int(_period(pd.DatetimeIndex([wstart]), "month")[0]), last_full + 2):
        if m > last_full and m not in orders.index:
            continue
        o = int(orders.get(m, 0))
        out.append({"month": _label(m, "month"), "partial": m > last_full,
                    "revenue_new": float(g_all.get(True, pd.Series(dtype=float)).get(m, 0.0)),
                    "revenue_returning": float(g_all.get(False, pd.Series(dtype=float)).get(m, 0.0)),
                    "customers_new": int(g_cust.get(True, pd.Series(dtype=int)).get(m, 0)),
                    "customers_returning": int(g_cust.get(False, pd.Series(dtype=int)).get(m, 0)),
                    "orders": o, "aov": float(gross.get(m, 0.0)) / o if o else None})
    return out
