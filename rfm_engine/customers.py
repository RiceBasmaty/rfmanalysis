"""Customer metrics (notebook 3.5) plus the attributes the report filters on.

One row per identified customer with a purchase in the window:
recency, frequency, gross revenue, returns, net revenue (Monetary), first-purchase history,
loyalty / consent, outlier flag, and the filter dimensions market / channel / loyalty.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .errors import stop
from .quality import Prepared

BOOL_MAP = {"true": True, "1": True, "yes": True, "y": True, "t": True,
            "false": False, "0": False, "no": False, "n": False, "f": False}


def to_bool(s: pd.Series) -> pd.Series:
    return s.astype("string").str.strip().str.lower().map(BOOL_MAP)


def build_customers(prep: Prepared) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (cust, outliers). cust is indexed by customer_key."""
    cfg, dq, txi, snap = prep.cfg, prep.dq, prep.txi, prep.snap
    p = txi[txi.transaction_type == "purchase"]
    r = txi[txi.transaction_type == "return"]
    cust = p.groupby("customer_key").agg(
        last_purchase=("transaction_date", "max"), first_purchase_window=("transaction_date", "min"),
        frequency=("transaction_id", "nunique"), gross_revenue=("amount_rc", "sum"))
    if cust.empty:
        stop("No purchases from identified customers", "Only returns or anonymous purchases were found.",
             "Check transaction_type and identifiers in the export.")
    cust["returns"] = r.groupby("customer_key").amount_rc.sum().reindex(cust.index).fillna(0)
    cust["monetary"] = cust.gross_revenue + cust.returns
    cust["aov"] = cust.gross_revenue / cust.frequency
    cust["recency_days"] = (snap - cust.last_purchase).dt.days
    only_ret = set(r.customer_key) - set(cust.index)
    dq.issue("customers", "customer with returns but no purchase in window", "info", len(only_ret),
             len(cust) + len(only_ret), "Excluded")
    dq.issue("customers", "net revenue ≤ 0 after returns (fully returned)", "info", int((cust.monetary <= 0).sum()),
             len(cust), "Kept; scored M = 1")

    cust["first_purchase_date"] = pd.NaT
    cust["loyalty_member"] = pd.Series(pd.NA, index=cust.index, dtype="object")
    cust["marketing_consent"] = pd.Series(pd.NA, index=cust.index, dtype="object")
    cu = prep.cu
    has_fpd = False
    if cu is not None:
        cu = cu[cu.customer_key.notna()].copy()
        dupk = cu.customer_key.duplicated()
        dq.issue("customers", "duplicate customer rows", "info", dupk.sum(), len(cu), "Earliest first_purchase_date kept")
        agg = {}
        if "first_purchase_date" in cu:
            cu["first_purchase_date"] = pd.to_datetime(cu.first_purchase_date.astype("string").str[:10], errors="coerce")
            agg["first_purchase_date"] = "min"
            has_fpd = bool(cu.first_purchase_date.notna().any())
        for c in ("loyalty_member", "marketing_consent"):
            if c in cu:
                cu[c] = to_bool(cu[c]); agg[c] = "max"
        if agg:
            enr = cu.groupby("customer_key").agg(agg)
            for c in enr.columns:
                cust[c] = enr[c].reindex(cust.index)
        matched = cust.index.isin(cu.customer_key)
        dq.issue("customers", "RFM customers not found in customers table", "info", (~matched).sum(), len(cust),
                 "No enrichment for these customers")
        later = cust.first_purchase_date > cust.first_purchase_window
        dq.issue("customers", "first_purchase_date later than first purchase in data", "warning", later.sum(), len(cust),
                 "Replaced by first purchase in data")
        cust.loc[later, "first_purchase_date"] = cust.loc[later, "first_purchase_window"]
    cust["has_prior_history"] = (cust.first_purchase_date < cust.first_purchase_window).fillna(False).astype(bool)

    # Outliers / B2B: only flag when far above the bulk
    q = cfg.outlier_quantile
    f_cut = max(cust.frequency.quantile(q), cust.frequency.quantile(0.99) * 3)
    m_cut = max(cust.monetary.quantile(q), cust.monetary.quantile(0.99) * 3)
    cust["is_outlier"] = (cust.frequency > f_cut) | (cust.monetary > m_cut)
    dq.issue("customers", f"outlier / possible B2B (F > {f_cut:,.0f} or M > {m_cut:,.0f} {cfg.reporting_currency})",
             "warning", cust.is_outlier.sum(), len(cust),
             "Excluded from scoring and reported separately" if cfg.outlier_action == "exclude" else "Kept")

    # ---------- filter dimensions ----------
    cust["market"] = _primary_market(p, cust.index, cfg.max_filter_markets) if prep.has_market else "All"
    cust["channel"] = _channel(p, cust.index) if prep.has_medium else "unknown"
    lm = cust.loyalty_member
    cust["loyalty"] = np.select([lm.isna().to_numpy(), lm.eq(True).to_numpy()], ["unknown", "member"], "non-member")

    # ---------- acquisition (cohorts) ----------
    use_fpd = cfg.cohort_start == "first_ever" and has_fpd
    acq = cust.first_purchase_date.where(cust.first_purchase_date.notna(), cust.first_purchase_window) if use_fpd \
        else cust.first_purchase_window
    cust["acquisition_date"] = acq
    # acquired in the window = a new customer we can follow (first-ever purchase inside the window)
    cust["acquired_in_window"] = acq >= prep.wstart
    later_p = p[["customer_key", "transaction_date"]].merge(acq.rename("acq"), left_on="customer_key", right_index=True)
    later_p = later_p[later_p.transaction_date > later_p.acq]
    cust["second_purchase_date"] = later_p.groupby("customer_key").transaction_date.min().reindex(cust.index)
    outliers = cust[cust.is_outlier].sort_values("monetary", ascending=False)
    cust.attrs["cohort_basis"] = "first_ever" if use_fpd else "first_in_window"
    cust.attrs["cohort_fallback"] = cfg.cohort_start == "first_ever" and not has_fpd
    return cust, outliers


def _primary_market(p: pd.DataFrame, index: pd.Index, max_markets: int) -> pd.Series:
    m = p.assign(market=p.market.astype("string").str.strip().str.upper().fillna("Unknown"))
    cnt = m.groupby(["customer_key", "market"]).size().rename("n").reset_index()
    last = m.groupby(["customer_key", "market"]).transaction_date.max().rename("last").reset_index()
    cnt = cnt.merge(last, on=["customer_key", "market"]).sort_values(["customer_key", "n", "last"], ascending=[True, False, False])
    prim = cnt.drop_duplicates("customer_key").set_index("customer_key").market.reindex(index).fillna("Unknown")
    top = prim.value_counts().index[:max_markets]
    return prim.where(prim.isin(top), "Other").astype(object)


def _channel(p: pd.DataFrame, index: pd.Index) -> pd.Series:
    med = p.medium.astype("string").str.lower()
    on = (med == "ecommerce").groupby(p.customer_key).any().reindex(index).fillna(False)
    st = (med == "retail").groupby(p.customer_key).any().reindex(index).fillna(False)
    return pd.Series(np.select([on & st, on, st], ["both", "online", "store"], "unknown"), index=index, dtype=object)
