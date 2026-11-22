"""DRAFT (prototype only): category data for the Categories tab, computed from sample_data.

    python prototype/categories_draft.py . out/report_cat.json && python prototype/build.py out/report_cat.json prototype/dist/index.html

The real engine module (rfm_engine/categories.py) comes after the look is approved."""
from __future__ import annotations

import itertools
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(sys.argv[1])
sys.path.insert(0, str(ROOT))
from rfm_engine import RunConfig  # noqa: E402
from rfm_engine.pipeline import _prepare_all, run_report  # noqa: E402
from rfm_engine.scoring import score, assign_segments  # noqa: E402
from rfm_engine.load import normalise_table  # noqa: E402
from rfm_engine.util import jsonable  # noqa: E402

DOMINANCE = 0.70
MAX_NAMED = 6
SMALL = 30
F_BINS = [1, 2, 3, 4, 6]

tables = {t: pd.read_csv(ROOT / f"sample_data/{t}.csv", dtype=str) for t in ("transactions", "customers", "items", "fx_rates")}
cfg = RunConfig(client_name="Dummy Nordic Retail", snapshot_date="2026-11-01")
res = run_report(tables, RunConfig(client_name="Dummy Nordic Retail", snapshot_date="2026-11-01"))
prep, cust, outliers, _w = _prepare_all(tables, cfg, lambda *a: None)
scored, th = score(cust, cfg, prep.snap)
scored, order = assign_segments(scored, cfg.segment_rules)
snap, wstart = prep.snap, prep.wstart

# ---- item lines of identified, cleaned transactions of scored customers ----
it = normalise_table(tables["items"], "items")
for c in ("item_price", "item_quantity", "item_discount"):
    it[c] = pd.to_numeric(it[c], errors="coerce").fillna(0.0)
t = prep.txi[prep.txi.customer_key.isin(scored.index)][["transaction_id", "customer_key", "transaction_date", "transaction_type", "rate", "month"]]
li = it.merge(t, on="transaction_id", how="inner")
li["rev"] = li.item_price * li.rate                       # net line revenue in reporting currency (returns negative)
li["disc"] = li.item_discount * li.rate
li["is_buy"] = li.transaction_type == "purchase"
cat_rev_total = li.groupby("item_category").rev.sum().sort_values(ascending=False)
CATS = cat_rev_total.index.tolist()
named = CATS[:MAX_NAMED]
li["cat"] = np.where(li.item_category.isin(named), li.item_category, "Other")
item_vs_tx = li.rev.sum() / prep.txi[prep.txi.customer_key.isin(scored.index)].amount_rc.sum() - 1

buys = li[li.is_buy]
# per customer x category facts
cc = li.groupby(["customer_key", "cat"]).agg(rev=("rev", "sum")).reset_index()
cco = buys.groupby(["customer_key", "cat"]).agg(orders=("transaction_id", "nunique"), last=("transaction_date", "max"),
                                                 first=("transaction_date", "min")).reset_index()
cc = cc.merge(cco, on=["customer_key", "cat"], how="inner")
cust_rev = cc.groupby("customer_key").rev.sum()
cust_orders = cc.groupby("customer_key").orders.sum()

# shopper type by revenue share (setting: revenue | orders)
def shopper_types(basis="revenue"):
    col = "rev" if basis == "revenue" else "orders"
    x = cc[cc[col] > 0]
    share = x[col] / x.groupby("customer_key")[col].transform("sum")
    top = x.assign(share=share).sort_values(["customer_key", "share"], ascending=[True, False]).drop_duplicates("customer_key")
    ncat = x.groupby("customer_key").cat.nunique()
    st = pd.Series(np.where(top.share.to_numpy() >= DOMINANCE, top.cat.to_numpy() + " shopper",
                            np.where(ncat.reindex(top.customer_key).to_numpy() >= 3, "Multi-category shopper", "Cross-shopper")),
                   index=top.customer_key.to_numpy())
    return st
SHOP = shopper_types("revenue")
NCAT = cc[cc.orders > 0].groupby("customer_key").cat.nunique()

# category-level RFM (per-category cut-offs, same segment rules)
CAT_SEG = {}
for c in CATS[:MAX_NAMED]:
    x = cc[(cc.cat == c) & (cc.orders > 0)].set_index("customer_key")
    d = pd.DataFrame({"recency_days": (snap - x["last"]).dt.days, "frequency": x.orders, "monetary": x.rev})
    re_ = d.recency_days.quantile([.2, .4, .6, .8]).round(0).tolist(); me = d.monetary.quantile([.2, .4, .6, .8]).round(2).tolist()
    d["R"] = (5 - np.searchsorted(re_, d.recency_days, side="left")).clip(1, 5)
    d["F"] = np.searchsorted(F_BINS, d.frequency, side="right").clip(1, 5)
    d["M"] = (1 + np.searchsorted(me, d.monetary, side="right")).clip(1, 5)
    d["has_prior_history"] = scored.has_prior_history.reindex(d.index).fillna(False).astype(bool)
    d, _ = assign_segments(d, cfg.segment_rules)
    CAT_SEG[c] = (d, {"recency_days_edges": re_, "monetary_edges": me})

# entry category = category with most revenue in the first order (acquired customers only)
acq = scored[scored.acquired_in_window]
fb = buys[buys.customer_key.isin(acq.index)].sort_values("transaction_date")
first_tx = fb.drop_duplicates("customer_key")[["customer_key", "transaction_id"]]
fo = fb.merge(first_tx, on=["customer_key", "transaction_id"])
ENTRY = fo.groupby(["customer_key", "cat"]).rev.sum().reset_index().sort_values(["customer_key", "rev"], ascending=[True, False]).drop_duplicates("customer_key").set_index("customer_key").cat
ords = fb.groupby(["customer_key", "transaction_id"]).agg(date=("transaction_date", "first")).reset_index().sort_values(["customer_key", "date"])
ords["n"] = ords.groupby("customer_key").cumcount()
second_tx = ords[ords.n == 1][["customer_key", "transaction_id"]]
so = fb.merge(second_tx, on=["customer_key", "transaction_id"])
SECOND = so.groupby(["customer_key", "cat"]).rev.sum().reset_index().sort_values(["customer_key", "rev"], ascending=[True, False]).drop_duplicates("customer_key").set_index("customer_key").cat

# purchase cycle: days between consecutive orders containing the category
cyc = buys.groupby(["customer_key", "cat", "transaction_id"]).transaction_date.first().reset_index().sort_values(["customer_key", "cat", "transaction_date"])
cyc["gap"] = cyc.groupby(["customer_key", "cat"]).transaction_date.diff().dt.days


def view(sc: pd.DataFrame) -> dict:
    keys = sc.index
    c = cc[cc.customer_key.isin(keys)]
    b = buys[buys.customer_key.isin(keys)]
    l = li[li.customer_key.isin(keys)]
    buyers = c[c.orders > 0]
    n_items = buyers.customer_key.nunique()
    tot_rev = c.rev.sum()
    ncat = NCAT.reindex(keys).dropna()
    rows = []
    for cat in [x for x in CATS[:MAX_NAMED] + (["Other"] if len(CATS) > MAX_NAMED else [])]:
        cb = buyers[buyers.cat == cat]; lc = l[l.cat == cat]; bc = b[b.cat == cat]
        if cb.empty:
            continue
        def kp(lines, bl, buyer_rows, label):
            gross = bl.rev.sum(); orders = bl.transaction_id.nunique()
            return {"name": label, "revenue": float(lines.rev.sum()), "revenue_share": float(lines.rev.sum() / tot_rev),
                    "customers": int(bl.customer_key.nunique()), "penetration": float(bl.customer_key.nunique() / len(keys)),
                    "orders": int(orders), "aov": float(gross / orders) if orders else None,
                    "items_per_order": float(bl.item_quantity.sum() / orders) if orders else None,
                    "avg_price": float(gross / bl.item_quantity.sum()) if bl.item_quantity.sum() else None,
                    "discount_rate": float(bl.disc.sum() / (gross + bl.disc.sum())) if gross else None,
                    "return_rate": float(-lines[lines.rev < 0].rev.sum() / gross) if gross else None,
                    "repeat_rate": float((bl.groupby("customer_key").transaction_id.nunique() >= 2).mean()),
                    "median_days_since": float((snap - bl.groupby("customer_key").transaction_date.max()).dt.days.median())}
        row = kp(lc, bc, cb, cat)
        row["subs"] = [kp(lc[lc.item_sub_category == s], bc[bc.item_sub_category == s], None, s)
                       for s in lc.groupby("item_sub_category").rev.sum().sort_values(ascending=False).index]
        for s in row["subs"]:
            s["small"] = s["customers"] < SMALL
        rows.append(row)
    cats = [r["name"] for r in rows]
    # segment mix by category (overall segments of each category's buyers)
    seg_mix = []
    for cat in cats:
        ks = buyers[buyers.cat == cat].customer_key
        vc = sc.segment.reindex(ks).value_counts()
        for s in order:
            seg_mix.append({"category": cat, "segment": s, "customers": int(vc.get(s, 0)), "share": float(vc.get(s, 0) / len(ks))})
    # breadth
    br = []
    rev_c = cust_rev.reindex(keys); ordc = sc.frequency
    for n in range(1, int(ncat.max()) + 1 if len(ncat) else 1):
        ks = ncat[ncat == n].index
        if len(ks) == 0:
            continue
        br.append({"n": n, "customers": int(len(ks)), "share": float(len(ks) / len(ncat)),
                   "revenue_per_customer": float(sc.monetary.reindex(ks).mean()), "repeat_rate": float((ordc.reindex(ks) >= 2).mean()),
                   "avg_orders": float(ordc.reindex(ks).mean())})
    # shopper types
    stp = SHOP.reindex(keys).dropna()
    st_rows = []
    tot_m = sc.monetary.reindex(stp.index).sum()
    for typ, ks in stp.groupby(stp).groups.items():
        m = sc.monetary.reindex(ks)
        st_rows.append({"type": typ, "customers": int(len(ks)), "customer_share": float(len(ks) / len(stp)),
                        "revenue_share": float(m.sum() / tot_m), "revenue_per_customer": float(m.mean()),
                        "repeat_rate": float((sc.frequency.reindex(ks) >= 2).mean()),
                        "champions_share": float((sc.segment.reindex(ks) == "Champions").mean())})
    st_rows.sort(key=lambda r: (r["type"].endswith("shopper") and not r["type"].startswith(("Cross", "Multi")), -r["customers"]))
    # cross-shopping matrix
    sets = buyers.groupby("cat").customer_key.apply(set).to_dict()
    cross = []
    for a in cats:
        for bcat in cats:
            A, B = sets.get(a, set()), sets.get(bcat, set())
            if not A:
                continue
            p_ab = len(A & B) / len(A); p_b = len(B) / n_items if n_items else 0
            cross.append({"a": a, "b": bcat, "share": p_ab if a != bcat else None, "lift": (p_ab / p_b) if (a != bcat and p_b) else None, "both": len(A & B)})
    # entry category
    ent = []
    links = {}
    ak = keys.intersection(ENTRY.index)
    age = (snap - sc.acquisition_date.reindex(ak)).dt.days
    days2 = (sc.second_purchase_date.reindex(ak) - sc.acquisition_date.reindex(ak)).dt.days
    for cat in cats:
        ks = ak[ENTRY.reindex(ak).to_numpy() == cat]
        if not len(ks):
            continue
        el = age.reindex(ks) >= 180
        el365 = age.reindex(ks) >= 365
        rev12 = l[l.customer_key.isin(ks[el365.to_numpy()])]
        acqd = sc.acquisition_date.reindex(rev12.customer_key).to_numpy()
        rev12 = rev12[(rev12.transaction_date.to_numpy() - acqd) <= np.timedelta64(365, "D")]
        ent.append({"category": cat, "customers": int(len(ks)), "back_180": float((days2.reindex(ks)[el.to_numpy()] <= 180).mean()) if el.any() else None,
                    "revenue_12m": float(rev12.rev.sum() / el365.sum()) if el365.any() else None})
        sec = SECOND.reindex(ks)
        for nxt, n in sec.fillna("No 2nd purchase yet").value_counts().items():
            links[(cat, nxt)] = int(n)
    sankey = [{"source": a, "target": (bb + " ·2nd" if bb != "No 2nd purchase yet" else bb), "value": v} for (a, bb), v in links.items()]
    # purchase cycle
    cy = cyc[cyc.customer_key.isin(keys)]
    cycle = []
    for cat in cats:
        g = cy[cy.cat == cat]
        firsts = g.groupby("customer_key").transaction_date.min()
        eligible180 = firsts[(snap - firsts).dt.days >= 180].index
        rep = g.dropna(subset=["gap"]).groupby("customer_key").gap.min()
        cycle.append({"category": cat, "median_days": float(g.gap.median()) if g.gap.notna().any() else None,
                      "back_90": float((rep.reindex(eligible180) <= 90).mean()) if len(eligible180) else None,
                      "back_180": float((rep.reindex(eligible180) <= 180).mean()) if len(eligible180) else None,
                      "buyers": int(firsts.size)})
    # trend
    tr = l.groupby(["month", "cat"]).rev.sum().unstack(fill_value=0.0).reindex(columns=cats, fill_value=0.0)
    trend = [{"month": m, **{k: float(v) for k, v in r.items()}} for m, r in tr.iterrows()]
    # category-level RFM
    cat_rfm = {}
    for cat in cats:
        if cat not in CAT_SEG:
            continue
        d, th_c = CAT_SEG[cat]
        d = d[d.index.isin(keys)]
        vc = d.segment.value_counts()
        tot = d.monetary.sum()
        segs = [{"segment": s, "customers": int(vc.get(s, 0)), "share": float(vc.get(s, 0) / len(d)) if len(d) else 0,
                 "revenue_share": float(d.monetary[d.segment == s].sum() / tot) if tot else 0} for s in order]
        vs = pd.crosstab(sc.segment.reindex(d.index), d.segment)
        cat_rfm[cat] = {"buyers": int(len(d)), "segments": segs,
                        "vs_overall": [{"overall": o, "category_segment": cs, "customers": int(vs.at[o, cs]) if (o in vs.index and cs in vs.columns) else 0}
                                       for o in order for cs in order],
                        "cutoffs": th_c}
    # cross-sell audiences: segment x category, never bought it, affinity lift >= 1.2
    aud = []
    lift = {(r["a"], r["b"]): r["lift"] for r in cross if r["lift"] is not None}
    bought = buyers.groupby("customer_key").cat.apply(set)
    for seg in ["Champions", "Loyal High Value", "Potential Loyalists", "New Customers", "At Risk High Value"]:
        ks = sc.index[sc.segment == seg]
        bk = bought.reindex(ks).dropna()
        for cat in cats:
            if cat == "Other":
                continue
            cand = bk[bk.apply(lambda s: cat not in s)]
            if cand.empty:
                continue
            aff = cand.apply(lambda s: max((lift.get((x, cat), 0) or 0) for x in s))
            sel = aff[aff >= 1.0].index
            if len(sel) < SMALL:
                continue
            cons = sc.marketing_consent.reindex(sel).fillna(False).astype(bool).sum()
            aud.append({"segment": seg, "category": cat, "customers": int(len(sel)), "consented": int(cons),
                        "affinity": float(aff.reindex(sel).mean()), "revenue_per_customer": float(sc.monetary.reindex(sel).mean()),
                        "penetration_gap": float(1 - len(sets.get(cat, set()) & set(ks)) / max(len(ks), 1))})
    aud.sort(key=lambda a: -(a["customers"] * a["revenue_per_customer"] * a["affinity"]))
    # insights
    ins = []
    if rows:
        top = max(rows, key=lambda r: r["revenue"])
        ins.append(f"<b>{top['name']}</b> is the largest category: <b>{top['revenue_share']:.0%}</b> of item revenue from {top['penetration']:.0%} of customers.")
    if len(br) >= 3:
        one, three = br[0], next((x for x in br if x["n"] == 3), br[-1])
        ins.append(f"<b>Breadth pays:</b> customers buying in {three['n']} categories spend <b>{three['revenue_per_customer'] / one['revenue_per_customer']:.1f}×</b> more than single-category customers ({three['repeat_rate']:.0%} vs {one['repeat_rate']:.0%} repeat rate).")
    pairs = sorted([r for r in cross if r["lift"]], key=lambda r: -r["lift"])
    if pairs:
        p = pairs[0]
        ins.append(f"<b>Strongest pair:</b> {p['a']} buyers are <b>{p['lift']:.2f}×</b> as likely as average to also buy {p['b']} ({p['share']:.0%} do).")
    if ent:
        e = [x for x in ent if x["back_180"] is not None]
        if e:
            best, worst = max(e, key=lambda x: x["back_180"]), min(e, key=lambda x: x["back_180"])
            ins.append(f"<b>Entry category matters:</b> {best['back_180']:.0%} of customers who start with {best['category']} come back within 180 days, vs {worst['back_180']:.0%} for {worst['category']}.")
    cyc_ok = [x for x in cycle if x["median_days"]]
    if cyc_ok:
        f = min(cyc_ok, key=lambda x: x["median_days"])
        ins.append(f"<b>Fastest cycle:</b> {f['category']} repeat buyers come back after a median of <b>{f['median_days']:.0f} days</b> — time replenishment reminders around then.")
    rr = [r for r in rows if r["return_rate"] is not None]
    if rr:
        r = max(rr, key=lambda x: x["return_rate"])
        ins.append(f"<b>Returns:</b> highest in {r['name']} ({r['return_rate']:.1%} of gross item revenue).")
    if aud:
        a = aud[0]
        ins.append(f"<b>Biggest cross-sell audience:</b> {a['customers']:,} {a['segment']} who never bought {a['category']} but have above-average affinity ({a['consented']:,} with consent).")
    return {"summary": {"categories": len(CATS), "sub_categories": int(li.item_sub_category.nunique()), "customers_with_items": int(n_items),
                        "coverage": float(n_items / len(keys)), "avg_categories": float(ncat.mean()) if len(ncat) else None,
                        "cross_share": float((ncat >= 2).mean()) if len(ncat) else None, "multi_share": float((ncat >= 3).mean()) if len(ncat) else None,
                        "cross_revenue_share": float(sc.monetary.reindex(ncat[ncat >= 2].index).sum() / sc.monetary.reindex(ncat.index).sum()) if len(ncat) else None},
            "kpis": rows, "segment_mix": seg_mix, "breadth": br, "shopper_types": st_rows, "cross": cross, "entry": ent, "sankey": sankey,
            "cycle": cycle, "trend": trend, "cat_rfm": cat_rfm, "audiences": aud[:12], "insights": ins, "categories": cats}


t0 = time.time()
opts = res.report["filters"]
mk = ["all"] + [o["value"] for o in opts["market"]]
ch = ["all"] + [o["value"] for o in opts["channel"]]
ly = ["all"] + [o["value"] for o in opts["loyalty"]]
cat_buyers = {c: set(cc[(cc.cat == c) & (cc.orders > 0)].customer_key) for c in CATS[:MAX_NAMED]}
views = {}
for m_, c_, l_, k_ in itertools.product(mk, ch, ly, ["all"] + CATS[:MAX_NAMED]):
    mask = np.ones(len(scored), bool)
    if m_ != "all": mask &= (scored.market == m_).to_numpy()
    if c_ != "all": mask &= (scored.channel == c_).to_numpy()
    if l_ != "all": mask &= (scored.loyalty == l_).to_numpy()
    if k_ != "all": mask &= scored.index.isin(cat_buyers[k_])
    if mask.sum() < 1:
        continue
    views[f"{m_}|{c_}|{l_}|{k_}"] = view(scored[mask])
print("views", len(views), "seconds", round(time.time() - t0, 1), file=sys.stderr)
report = res.report
report["categories"] = jsonable({"meta": {"basis": "revenue", "dominance": DOMINANCE, "max_named": MAX_NAMED, "small": SMALL,
                                          "item_vs_transaction_gap": float(item_vs_tx), "categories": CATS,
                                          "subs": int(li.item_sub_category.nunique())}, "views": views}, digits=4)
report["filters"]["category"] = [{"value": c, "label": c, "customers": int(len(cat_buyers[c]))} for c in CATS[:MAX_NAMED]]
json.dump(report, open(sys.argv[2], "w"), separators=(",", ":"))
print("MB", round(Path(sys.argv[2]).stat().st_size / 1e6, 2), file=sys.stderr)
