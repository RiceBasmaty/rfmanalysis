"""L0 — data health check: field mapping, cleaning, FX, trims, identity linking, items check.

A faithful port of notebook sections 3.1–3.4. Every finding is logged with a severity:
critical (client must fix the export), warning (fixed automatically), info (for awareness).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .config import RunConfig
from .errors import stop
from .spec import SPEC

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


@dataclass
class DQLog:
    issues: list[dict[str, Any]] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)

    def issue(self, table: str, check: str, severity: str, rows: int, total: int, action: str) -> None:
        rows = int(rows)
        self.issues.append(dict(table=table, check=check, severity=severity, rows=rows,
                                share=(rows / total if total else 0.0), action=action))

    def log(self, step: str, before: int, after: int, kind: str = "cleaning") -> None:
        self.steps.append(dict(step=step, rows_before=int(before), rows_after=int(after),
                               removed=int(before - after), kind=kind))

    def issues_df(self) -> pd.DataFrame:
        df = pd.DataFrame(self.issues, columns=["table", "check", "severity", "rows", "share", "action"])
        df = df[df.rows > 0].copy()
        df["_o"] = df.severity.map(SEVERITY_ORDER)
        return df.sort_values(["_o", "rows"], ascending=[True, False]).drop(columns="_o").reset_index(drop=True)

    def steps_df(self) -> pd.DataFrame:
        return pd.DataFrame(self.steps, columns=["step", "rows_before", "rows_after", "removed", "kind"])

    def counts(self) -> dict[str, int]:
        df = self.issues_df()
        return {s: int((df.severity == s).sum()) for s in SEVERITY_ORDER}


@dataclass
class Prepared:
    """Everything L0 produces; input for customer metrics, scoring and the reports."""
    cfg: RunConfig
    dq: DQLog
    snap: pd.Timestamp
    wstart: pd.Timestamp
    n_received: int
    tx: pd.DataFrame                  # clean, in window, trimmed; identified and anonymous
    txi: pd.DataFrame                 # identified rows only (customer_key set)
    cu: pd.DataFrame | None           # cleaned customers table with customer_key
    currencies: list[str]
    coverage: dict[str, float]
    linked_rows: int
    mapping: pd.DataFrame
    extras: dict[str, list[str]]
    loaded: list[dict[str, Any]]
    trim_options: dict[str, Any]
    trim_log: list[dict[str, Any]]
    items: dict[str, Any] | None
    has_medium: bool
    has_market: bool


# ---------------- helpers ----------------
def _clean_hash(s: pd.Series, table: str, dq: DQLog) -> pd.Series:
    s = s.astype("string").str.strip()
    n = s.notna().sum()
    pii = s.str.contains("@", na=False)
    dq.issue(table, "hashed_email contains plain email (PII)", "critical", pii.sum(), n,
             "Values removed; client must hash before sharing")
    upper = s.str.fullmatch(r"[0-9A-Fa-f]{64}", na=False) & ~s.str.fullmatch(r"[0-9a-f]{64}", na=False)
    dq.issue(table, "hashed_email in uppercase", "warning", upper.sum(), n, "Lowercased")
    s = s.str.lower()
    bad = s.notna() & ~s.str.fullmatch(r"[0-9a-f]{64}", na=False)
    dq.issue(table, "hashed_email not a 64-char SHA-256 hex", "warning", (bad & ~pii).sum(), n,
             "Set to empty; row falls back to customer_id")
    return s.where(~bad, pd.NA)


def _strip_na(s: pd.Series) -> pd.Series:
    return s.astype("string").str.strip().replace("", pd.NA)


def _most_frequent_email(pairs: pd.DataFrame) -> pd.Series:
    """customer_id -> its most frequent hashed_email (ties: the one seen first). Vectorised."""
    p = pairs.astype(object).reset_index(drop=True)
    p["pos"] = np.arange(len(p))
    g = p.groupby(["customer_id", "hashed_email"], sort=False).agg(n=("pos", "size"), first=("pos", "min")).reset_index()
    g = g.sort_values(["customer_id", "n", "first"], ascending=[True, False, True], kind="mergesort")
    return g.drop_duplicates("customer_id").set_index("customer_id").hashed_email


def field_mapping(raw: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    rows, extras = [], {}
    for t, cols in SPEC.items():
        present = set(raw[t].columns) if t in raw else set()
        for col, (typ, req, _desc) in cols.items():
            if t not in raw:
                st = "table not provided"
            elif col in present:
                st = "present"
            else:
                st = "missing" + (" (required)" if req.startswith("Required") else "")
            rows.append(dict(table=t, column=col, type=typ, requirement=req, status=st))
        if t in raw and present - set(cols):
            extras[t] = sorted(present - set(cols))
    return pd.DataFrame(rows), extras


# ---------------- main ----------------
def prepare(raw: dict[str, pd.DataFrame], cfg: RunConfig) -> Prepared:
    """raw: normalised tables (see load.normalise_table). Raises RFMStop on blocking problems."""
    if "transactions" not in raw or raw["transactions"] is None or raw["transactions"].empty:
        stop("Transactions table not found", "The transactions table is missing or empty.",
             "Connect the transactions table — it is the only required one.")
    raw = {k: v.copy() for k, v in raw.items() if v is not None}
    loaded = [dict(table=t, rows=int(len(raw[t])), columns=int(raw[t].shape[1])) for t in SPEC if t in raw]

    # ---- 3.1 field mapping ----
    mapping, extras = field_mapping(raw)
    tx_cols = set(raw["transactions"].columns)
    critical_missing = [c for c in ["transaction_id", "transaction_date", "transaction_amount"] if c not in tx_cols]
    if not ({"hashed_email", "customer_id"} & tx_cols):
        critical_missing.append("hashed_email or customer_id")
    if critical_missing:
        stop("Required columns missing in transactions", ", ".join(critical_missing),
             "Ask the client to add them, or map them to the right client columns on the Connect step.")
    for c in SPEC["transactions"]:
        if c not in raw["transactions"].columns:
            raw["transactions"][c] = None

    dq = DQLog()
    tx = raw["transactions"]
    n0 = len(tx)

    # ---- 3.2 cleaning ----
    n = len(tx); dup = tx.duplicated()
    dq.issue("transactions", "exact duplicate rows", "warning", dup.sum(), n, "Removed")
    tx = tx[~dup]; dq.log("remove exact duplicates", n, len(tx))
    n = len(tx); dup_id = tx.transaction_id.notna() & tx.transaction_id.duplicated(keep="first")
    dq.issue("transactions", "same transaction_id, different content", "warning", dup_id.sum(), n, "First row kept")
    tx = tx[~dup_id]; dq.log("remove conflicting duplicate ids", n, len(tx))
    dq.issue("transactions", "transaction_id empty", "warning", tx.transaction_id.isna().sum(), len(tx), "Rows removed")
    n = len(tx); tx = tx[tx.transaction_id.notna()]; dq.log("remove empty transaction_id", n, len(tx))

    tx = tx.copy()
    tx["transaction_date"] = pd.to_datetime(tx.transaction_date.astype("string").str[:10], errors="coerce", format="%Y-%m-%d")
    bad_date = tx.transaction_date.isna()
    dq.issue("transactions", "transaction_date missing or not YYYY-MM-DD",
             "warning" if bad_date.mean() < 0.05 else "critical", bad_date.sum(), len(tx), "Rows removed")
    n = len(tx); tx = tx[~bad_date]; dq.log("remove invalid dates", n, len(tx))
    if tx.empty:
        stop("No valid dates", "No transaction_date could be read as YYYY-MM-DD.", "Ask the client for ISO dates (2026-01-31).")

    snap = pd.Timestamp(cfg.snapshot_date) if cfg.snapshot_date else tx.transaction_date.max() + pd.Timedelta(days=1)
    wstart = snap - pd.DateOffset(months=cfg.lookback_months)
    fut = tx.transaction_date >= snap
    old = tx.transaction_date < wstart
    dq.issue("transactions", f"date on/after snapshot {snap.date()}", "warning" if fut.mean() < 0.01 else "critical",
             fut.sum(), len(tx), "Rows removed")
    dq.issue("transactions", f"date before window start {wstart.date()}", "info", old.sum(), len(tx), "Outside window; removed")
    n = len(tx); tx = tx[~fut & ~old]; dq.log("keep analysis window", n, len(tx))
    if tx.empty:
        stop("No transactions in the analysis window",
             f"Window {wstart.date()} → {(snap - pd.Timedelta(days=1)).date()} is empty.",
             "Check the snapshot date and lookback window in Settings.")

    amt = pd.to_numeric(tx.transaction_amount.astype("string").str.replace(",", ".", regex=False)
                        .str.replace(r"\s", "", regex=True), errors="coerce")
    dq.issue("transactions", "transaction_amount missing or non-numeric", "warning", amt.isna().sum(), len(tx), "Rows removed")
    tx["transaction_amount"] = amt
    n = len(tx); tx = tx[amt.notna()]; dq.log("remove invalid amounts", n, len(tx))

    tt = tx.transaction_type.astype("string").str.strip().str.lower()
    tt = tt.replace({"refund": "return", "sale": "purchase", "order": "purchase"})
    sign_type = pd.Series(np.where(tx.transaction_amount < 0, "return", "purchase"), index=tx.index)
    if tt.isna().all():
        tt = sign_type
        dq.issue("transactions", "transaction_type not provided", "info", len(tx), len(tx), "Inferred from sign of amount")
    else:
        unk = ~tt.isin(["purchase", "return"])
        dq.issue("transactions", "transaction_type not purchase/return", "warning", unk.sum(), len(tx), "Inferred from sign of amount")
        tt = tt.where(~unk, sign_type)
    tx["transaction_type"] = tt.astype(object)
    neg_p = (tx.transaction_type == "purchase") & (tx.transaction_amount < 0)
    pos_r = (tx.transaction_type == "return") & (tx.transaction_amount > 0)
    dq.issue("transactions", "purchase with negative amount", "warning", neg_p.sum(), len(tx), "Treated as return")
    dq.issue("transactions", "return with positive amount", "warning", pos_r.sum(), len(tx), "Sign flipped to negative")
    tx.loc[neg_p, "transaction_type"] = "return"
    tx.loc[pos_r, "transaction_amount"] *= -1
    zero = (tx.transaction_type == "purchase") & (tx.transaction_amount == 0)
    dq.issue("transactions", "purchase with zero amount", "info", zero.sum(), len(tx), "Kept (counts towards Frequency)")
    ret = tx.transaction_type == "return"
    dq.issue("transactions", "return without original_transaction_id", "info",
             (ret & tx.original_transaction_id.isna()).sum(), ret.sum(), "Kept; reduces Monetary in its own month")

    tx["hashed_email"] = _clean_hash(tx.hashed_email, "transactions", dq)
    tx["customer_id"] = _strip_na(tx.customer_id)

    cur = tx.currency.astype("string").str.strip()
    lower = cur.notna() & (cur != cur.str.upper())
    dq.issue("transactions", "currency not uppercase", "warning", lower.sum(), len(tx), "Uppercased")
    cur = cur.str.upper()
    miss = cur.isna()
    has_market = bool(tx.market.notna().any())
    if miss.any() and has_market:
        mkt_cur = pd.DataFrame({"m": tx.market, "c": cur}).dropna().groupby("m").c.agg(lambda s: s.mode().iat[0])
        cur = cur.fillna(tx.market.map(mkt_cur))
    dq.issue("transactions", "currency missing", "warning", miss.sum(), len(tx),
             "Filled from the market's main currency" if has_market else "Assumed reporting currency")
    rc = cfg.reporting_currency
    tx["currency"] = cur.fillna(rc).astype(object)

    currencies = sorted(tx.currency.unique())
    if len(currencies) > 1 or currencies[0] != rc:
        if "fx_rates" not in raw:
            stop("FX rates needed", f"The data has currencies {', '.join(currencies)} but no fx_rates table was provided.",
                 f"Add an fx_rates table (month, currency, rate to {rc}) on the Connect step.")
        fx = raw["fx_rates"].copy()
        for c in ("month", "currency", "rate"):
            if c not in fx.columns:
                stop("FX rates table incomplete", f"Column “{c}” is missing.", "The FX table needs month, currency and rate.")
        fx["currency"] = fx.currency.astype("string").str.strip().str.upper()
        fx["rate"] = pd.to_numeric(fx.rate, errors="coerce")
        fx["month"] = fx.month.astype("string").str[:7]
        fx = fx.dropna(subset=["month", "currency", "rate"]).drop_duplicates(["month", "currency"])
        fx = fx.astype({"month": object, "currency": object})
        tx["month"] = tx.transaction_date.dt.strftime("%Y-%m")
        tx = tx.merge(fx[["month", "currency", "rate"]], on=["month", "currency"], how="left")
        if tx.rate.isna().any():
            last_rate = fx.sort_values("month").groupby("currency").rate.last()
            tx["rate"] = tx.rate.fillna(tx.currency.map(last_rate))
        is_rc = tx.currency == rc
        tx.loc[is_rc, "rate"] = tx.loc[is_rc, "rate"].fillna(1.0)
        nofx = tx.rate.isna()
        dq.issue("transactions", "no FX rate for currency", "critical" if nofx.mean() > 0.01 else "warning",
                 nofx.sum(), len(tx), "Rows removed")
        n = len(tx); tx = tx[~nofx]; dq.log("remove rows without FX rate", n, len(tx))
    else:
        tx["rate"] = 1.0
        tx["month"] = tx.transaction_date.dt.strftime("%Y-%m")
    tx["amount_rc"] = tx.transaction_amount * tx.rate

    # ---- trims (chosen on the data-check screen) ----
    trim_options = _trim_options(tx)
    tx, trim_log = _apply_trims(tx, cfg.trims, dq)
    if tx.empty:
        stop("Trims removed every transaction", "No rows are left after the trims.", "Loosen the trims on the data check.")

    # ---- 3.3 identity ----
    cu = raw.get("customers")
    if cu is not None:
        cu = cu.copy()
        cu["hashed_email"] = _clean_hash(cu["hashed_email"] if "hashed_email" in cu else pd.Series(index=cu.index, dtype="string"),
                                         "customers", dq)
        cu["customer_id"] = _strip_na(cu["customer_id"] if "customer_id" in cu else pd.Series(index=cu.index, dtype="string"))

    pairs = [tx[["customer_id", "hashed_email"]]]
    if cu is not None:
        pairs.append(cu[["customer_id", "hashed_email"]])
    pairs = pd.concat(pairs).dropna()
    if len(pairs):
        cid2email = _most_frequent_email(pairs)
        conflict = pairs.drop_duplicates().groupby("customer_id").hashed_email.nunique()
    else:
        cid2email, conflict = pd.Series(dtype="string"), pd.Series(dtype=int)
    dq.issue("identity", "customer_id linked to several emails", "info", (conflict > 1).sum(), len(conflict), "Most frequent email used")

    linked = tx.hashed_email.isna() & tx.customer_id.map(cid2email).notna()
    tx["customer_key"] = tx.hashed_email.fillna(tx.customer_id.map(cid2email))
    tx["customer_key"] = tx.customer_key.fillna("cid:" + tx.customer_id)

    ident = tx.customer_key.notna()
    purch = tx.transaction_type == "purchase"
    med = tx.medium.astype("string").str.strip().str.lower()
    tx["medium"] = med.astype(object)
    has_medium = bool(med.notna().any())
    if has_medium:
        ecom_anon = (med == "ecommerce") & ~ident
        dq.issue("transactions", "ecommerce transaction without any identifier", "warning", ecom_anon.sum(),
                 (med == "ecommerce").sum(), "Excluded from RFM (check the export)")
    dq.issue("transactions", "transaction without identifier (anonymous)", "info", (~ident).sum(), len(tx), "Excluded from RFM")
    dq.issue("identity", "rows linked via customer_id → email", "info", linked.sum(), len(tx), "Merged into the email's customer")

    total_rev = tx.amount_rc.sum()
    coverage = {
        "Purchases identified": float((ident & purch).sum() / max(purch.sum(), 1)),
        "Revenue identified": float(tx.loc[ident, "amount_rc"].sum() / total_rev) if total_rev else 0.0,
    }
    if has_medium:
        rp = purch & (med == "retail")
        if rp.any():
            coverage["Retail purchases linked"] = float((rp & ident).sum() / rp.sum())
        ep = purch & (med == "ecommerce")
        if ep.any():
            coverage["Ecommerce purchases linked"] = float((ep & ident).sum() / ep.sum())

    n = len(tx); txi = tx[ident].copy(); dq.log("keep identified transactions", n, len(txi))
    if txi.empty:
        stop("No identified customers", "No transaction has a usable hashed_email or customer_id.",
             "Check the identifier columns in the export (see the data-quality findings for hash problems).")

    if cu is not None:
        cu["customer_key"] = cu.hashed_email.fillna(cu.customer_id.map(cid2email)).fillna("cid:" + cu.customer_id)

    # ---- 3.4 items ----
    items = _items_check(raw, tx, cfg, dq)

    return Prepared(cfg=cfg, dq=dq, snap=snap, wstart=wstart, n_received=n0, tx=tx, txi=txi, cu=cu,
                    currencies=currencies, coverage=coverage, linked_rows=int(linked.sum()), mapping=mapping,
                    extras=extras, loaded=loaded, trim_options=trim_options, trim_log=trim_log, items=items,
                    has_medium=has_medium, has_market=has_market)


def _trim_options(tx: pd.DataFrame) -> dict[str, Any]:
    def counts(col: str) -> list[dict[str, Any]]:
        s = tx[col].astype("string").str.strip()
        if s.isna().all():
            return []
        vc = s.fillna("(empty)").value_counts()
        return [{"value": k, "rows": int(v)} for k, v in vc.items()]
    p = tx[tx.transaction_type == "purchase"].amount_rc
    return {
        "months": sorted(tx.month.unique().tolist()),
        "markets": counts("market"),
        "mediums": counts("medium"),
        "stores": counts("store_id")[:200],
        "amount_quantiles": {q: float(p.quantile(float(q))) for q in ("0.001", "0.01", "0.5", "0.99", "0.999")} if len(p) else {},
    }


def _apply_trims(tx: pd.DataFrame, t: dict[str, Any], dq: DQLog) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    log: list[dict[str, Any]] = []

    def cut(mask: pd.Series, label: str) -> None:
        nonlocal tx
        n = len(tx); tx = tx[~mask]
        removed = n - len(tx)
        log.append({"trim": label, "removed": int(removed)})
        dq.log(f"trim: {label}", n, len(tx), kind="trim")

    if t.get("date_from"):
        start = pd.Timestamp(t["date_from"] + ("-01" if len(t["date_from"]) == 7 else ""))
        cut(tx.transaction_date < start, f"before {t['date_from']}")
    if t.get("date_to"):
        end = pd.Timestamp(t["date_to"] + ("-01" if len(t["date_to"]) == 7 else ""))
        if len(t["date_to"]) == 7:
            end = end + pd.offsets.MonthEnd(0)
        cut(tx.transaction_date > end, f"after {t['date_to']}")
    for key, col, label in (("exclude_markets", "market", "market"), ("exclude_mediums", "medium", "channel"),
                            ("exclude_stores", "store_id", "store")):
        vals = set(t.get(key) or [])
        if vals:
            s = tx[col].astype("string").str.strip().fillna("(empty)")
            if col == "medium":
                s = s.str.lower(); vals = {v.lower() for v in vals}
            cut(s.isin(vals).fillna(False).astype(bool), f"{label} {', '.join(sorted(vals))}")
    purch = tx.transaction_type == "purchase"
    if t.get("min_amount") is not None:
        cut(purch & (tx.amount_rc < t["min_amount"]), f"purchases below {t['min_amount']:,.0f}")
        purch = tx.transaction_type == "purchase"
    if t.get("max_amount") is not None:
        cut(purch & (tx.amount_rc > t["max_amount"]), f"purchases above {t['max_amount']:,.0f}")
    return tx, log


def _items_check(raw: dict[str, pd.DataFrame], tx: pd.DataFrame, cfg: RunConfig, dq: DQLog) -> dict[str, Any] | None:
    if not ("items" in raw and "item_price" in raw["items"].columns and "transaction_id" in raw["items"].columns):
        return None
    tol = cfg.reconciliation_tolerance_pct / 100
    it = raw["items"].copy()
    it["item_price"] = pd.to_numeric(it.item_price, errors="coerce")
    orphan = ~it.transaction_id.isin(raw["transactions"].transaction_id)
    dq.issue("items", "item line without matching transaction", "warning", orphan.sum(), len(it), "Ignored")
    dq.issue("items", "item_price missing or non-numeric", "warning", it.item_price.isna().sum(), len(it), "Ignored in reconciliation")
    s = it.groupby("transaction_id").item_price.sum()
    rec = tx.set_index("transaction_id").transaction_amount.to_frame().join(s.rename("items_sum"), how="inner")
    diff = (rec.transaction_amount - rec.items_sum).abs() > tol * rec.transaction_amount.abs().clip(lower=1)
    dq.issue("items", f"transaction_amount ≠ sum of item lines (>{tol:.0%})", "warning", diff.sum(), len(rec),
             "Transaction amount used for RFM; check VAT/shipping basis")
    no_items = ~tx.transaction_id.isin(it.transaction_id)
    dq.issue("items", "transaction without item lines", "info", no_items.sum(), len(tx), "Relevant for L3")
    return {"matched": int(len(rec)), "matched_share": float(len(rec) / len(tx)) if len(tx) else 0.0,
            "amounts_match": float(1 - diff.mean()) if len(rec) else None, "orphans": int(orphan.sum()),
            "tolerance": tol}
