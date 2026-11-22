"""DRAFT (prototype only): data for the Categories tab, computed from sample_data.

    python prototype/categories_draft.py . out/report_cat.json && python prototype/build.py out/report_cat.json prototype/dist/index.html

The page tells one story in three chapters: where we stand (situation), what's changing (complication,
time lens) and what to do (resolution: ranked moves, audiences, CRM segments).
The real engine module (rfm_engine/categories.py) replaces this file after the look is approved."""
from __future__ import annotations

import html
import itertools
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT))
from rfm_engine import RunConfig  # noqa: E402
from rfm_engine.load import normalise_table  # noqa: E402
from rfm_engine.pipeline import _prepare_all, run_report  # noqa: E402
from rfm_engine.scoring import assign_segments, score  # noqa: E402
from rfm_engine.util import jsonable, money  # noqa: E402

DOMINANCE, MAX_NAMED, SMALL, F_BINS = 0.70, 6, 30, [1, 2, 3, 4, 6]
e = html.escape

tables = {t: pd.read_csv(ROOT / f"sample_data/{t}.csv", dtype=str) for t in ("transactions", "customers", "items", "fx_rates")}
cfg = RunConfig(client_name="Dummy Nordic Retail", snapshot_date="2026-11-01")
res = run_report(tables, RunConfig(client_name="Dummy Nordic Retail", snapshot_date="2026-11-01"))
prep, cust, _o, _w = _prepare_all(tables, cfg, lambda *a: None)
scored, _th = score(cust, cfg, prep.snap)
scored, order = assign_segments(scored, cfg.segment_rules)
snap, wstart, CUR = prep.snap, prep.wstart, cfg.reporting_currency
HALF = cfg.lookback_months // 2                      # growth = last half of the window vs the half before
split = snap - pd.DateOffset(months=HALF)

# ---------- item lines of identified, cleaned transactions of scored customers ----------
it = normalise_table(tables["items"], "items")
for c in ("item_price", "item_quantity", "item_discount"):
    it[c] = pd.to_numeric(it[c], errors="coerce").fillna(0.0)
t = prep.txi[prep.txi.customer_key.isin(scored.index)][["transaction_id", "customer_key", "transaction_date", "transaction_type", "rate", "month"]]
li = it.merge(t, on="transaction_id", how="inner")
li["rev"], li["disc"] = li.item_price * li.rate, li.item_discount * li.rate
li["recent"] = li.transaction_date >= split
CATS = li.groupby("item_category").rev.sum().sort_values(ascending=False).index.tolist()
named = CATS[:MAX_NAMED]
li["cat"] = np.where(li.item_category.isin(named), li.item_category, "Other")
item_vs_tx = li.rev.sum() / prep.txi[prep.txi.customer_key.isin(scored.index)].amount_rc.sum() - 1
buys = li[li.transaction_type == "purchase"]

cc = li.groupby(["customer_key", "cat"]).agg(rev=("rev", "sum")).reset_index()
cco = buys.groupby(["customer_key", "cat"]).agg(orders=("transaction_id", "nunique"), last=("transaction_date", "max")).reset_index()
cc = cc.merge(cco, on=["customer_key", "cat"], how="inner")
NCAT = cc[cc.orders > 0].groupby("customer_key").cat.nunique()


def shopper_types(basis="revenue"):
    col = "rev" if basis == "revenue" else "orders"
    x = cc[cc[col] > 0]
    share = x[col] / x.groupby("customer_key")[col].transform("sum")
    top = x.assign(share=share).sort_values(["customer_key", "share"], ascending=[True, False]).drop_duplicates("customer_key")
    n = x.groupby("customer_key").cat.nunique().reindex(top.customer_key).to_numpy()
    return pd.Series(np.where(top.share.to_numpy() >= DOMINANCE, top.cat.to_numpy() + " shopper",
                              np.where(n >= 3, "Multi-category shopper", "Cross-shopper")), index=top.customer_key.to_numpy())


SHOP = shopper_types("revenue")

# category-level RFM: per-category cut-offs, same segment rules
CAT_SEG = {}
for cat in named:
    x = cc[(cc.cat == cat) & (cc.orders > 0)].set_index("customer_key")
    d = pd.DataFrame({"recency_days": (snap - x["last"]).dt.days, "frequency": x.orders, "monetary": x.rev})
    re_, me = d.recency_days.quantile([.2, .4, .6, .8]).round(0).tolist(), d.monetary.quantile([.2, .4, .6, .8]).round(2).tolist()
    d["R"] = (5 - np.searchsorted(re_, d.recency_days, side="left")).clip(1, 5)
    d["F"] = np.searchsorted(F_BINS, d.frequency, side="right").clip(1, 5)
    d["M"] = (1 + np.searchsorted(me, d.monetary, side="right")).clip(1, 5)
    d["has_prior_history"] = scored.has_prior_history.reindex(d.index).fillna(False).astype(bool)
    d, _ = assign_segments(d, cfg.segment_rules)
    d["consent"] = scored.marketing_consent.reindex(d.index).fillna(False).astype(bool)
    CAT_SEG[cat] = d

# entry and second category (acquired customers)
acq = scored[scored.acquired_in_window]
fb = buys[buys.customer_key.isin(acq.index)].sort_values("transaction_date")
ords = fb.groupby(["customer_key", "transaction_id"]).agg(date=("transaction_date", "first")).reset_index().sort_values(["customer_key", "date"])
ords["n"] = ords.groupby("customer_key").cumcount()


def main_cat(nth):
    o = fb.merge(ords[ords.n == nth][["customer_key", "transaction_id"]], on=["customer_key", "transaction_id"])
    return o.groupby(["customer_key", "cat"]).rev.sum().reset_index().sort_values(["customer_key", "rev"], ascending=[True, False]).drop_duplicates("customer_key").set_index("customer_key").cat


ENTRY, SECOND = main_cat(0), main_cat(1)
cyc = buys.groupby(["customer_key", "cat", "transaction_id"]).transaction_date.first().reset_index().sort_values(["customer_key", "cat", "transaction_date"])
cyc["gap"] = cyc.groupby(["customer_key", "cat"]).transaction_date.diff().dt.days


def pctf(v, signed=False):
    if v is None:
        return "n/a"
    return ("+" if signed and v > 0 else "") + f"{v:.0%}"


def view(sc: pd.DataFrame) -> dict:
    keys = sc.index
    c, b, l = cc[cc.customer_key.isin(keys)], buys[buys.customer_key.isin(keys)], li[li.customer_key.isin(keys)]
    buyers = c[c.orders > 0]
    n_items, tot_rev = buyers.customer_key.nunique(), c.rev.sum()
    ncat = NCAT.reindex(keys).dropna()

    # ---------- 01 · where we stand ----------
    def kp(lines, bl, label):
        gross, orders = bl.rev.sum(), bl.transaction_id.nunique()
        r_now, r_old = lines[lines.recent].rev.sum(), lines[~lines.recent].rev.sum()
        b_now, b_old = bl[bl.recent].customer_key.nunique(), bl[~bl.recent].customer_key.nunique()
        return {"name": label, "revenue": float(lines.rev.sum()), "revenue_share": float(lines.rev.sum() / tot_rev),
                "customers": int(bl.customer_key.nunique()), "penetration": float(bl.customer_key.nunique() / len(keys)),
                "aov": float(gross / orders) if orders else None,
                "discount_rate": float(bl.disc.sum() / (gross + bl.disc.sum())) if gross else None,
                "return_rate": float(-lines[lines.rev < 0].rev.sum() / gross) if gross else None,
                "repeat_rate": float((bl.groupby("customer_key").transaction_id.nunique() >= 2).mean()),
                "growth": float(r_now / r_old - 1) if r_old > 0 else None,
                "buyers_growth": float(b_now / b_old - 1) if b_old else None,
                "rpb_growth": float((r_now / b_now) / (r_old / b_old) - 1) if b_now and b_old and r_old > 0 else None,
                "lapsing": float((sc.R.reindex(bl.customer_key.unique()) <= 2).mean()) if len(bl) else None}
    rows = []
    for cat in named + (["Other"] if len(CATS) > MAX_NAMED else []):
        lc, bc = l[l.cat == cat], b[b.cat == cat]
        if bc.empty:
            continue
        row = kp(lc, bc, cat)
        row["subs"] = [{**kp(lc[lc.item_sub_category == s], bc[bc.item_sub_category == s], s)}
                       for s in lc.groupby("item_sub_category").rev.sum().sort_values(ascending=False).index]
        for s in row["subs"]:
            s["small"] = s["customers"] < SMALL
        rows.append(row)
    cats = [r["name"] for r in rows]
    K = {r["name"]: r for r in rows}

    br = []
    for n in range(1, int(ncat.max()) + 1 if len(ncat) else 1):
        ks = ncat[ncat == n].index
        if len(ks):
            br.append({"n": n, "customers": int(len(ks)), "share": float(len(ks) / len(ncat)),
                       "revenue_per_customer": float(sc.monetary.reindex(ks).mean()), "repeat_rate": float((sc.frequency.reindex(ks) >= 2).mean()),
                       "avg_orders": float(sc.frequency.reindex(ks).mean())})
    stp = SHOP.reindex(keys).dropna()
    tot_m = sc.monetary.reindex(stp.index).sum()
    st_rows = [{"type": typ, "customers": int(len(ks)), "customer_share": float(len(ks) / len(stp)),
                "revenue_share": float(sc.monetary.reindex(ks).sum() / tot_m), "revenue_per_customer": float(sc.monetary.reindex(ks).mean()),
                "repeat_rate": float((sc.frequency.reindex(ks) >= 2).mean())} for typ, ks in stp.groupby(stp).groups.items()]

    # ---------- 02 · what's changing ----------
    seg_mix = []
    for cat in cats:
        ks = buyers[buyers.cat == cat].customer_key
        vc = sc.segment.reindex(ks).value_counts()
        seg_mix += [{"category": cat, "segment": s, "customers": int(vc.get(s, 0)), "share": float(vc.get(s, 0) / len(ks))} for s in order]
    cy = cyc[cyc.customer_key.isin(keys)]
    cycle = []
    for cat in cats:
        g = cy[cy.cat == cat]
        firsts = g.groupby("customer_key").transaction_date.min()
        el = firsts[(snap - firsts).dt.days >= 180].index
        rep = g.dropna(subset=["gap"]).groupby("customer_key").gap.min()
        cycle.append({"category": cat, "median_days": float(g.gap.median()) if g.gap.notna().any() else None,
                      "back_90": float((rep.reindex(el) <= 90).mean()) if len(el) else None,
                      "back_180": float((rep.reindex(el) <= 180).mean()) if len(el) else None, "buyers": int(firsts.size)})
    tr = l.groupby(["month", "cat"]).rev.sum().unstack(fill_value=0.0).reindex(columns=cats, fill_value=0.0)
    trend = [{"month": m, **{k: float(v) for k, v in r.items()}} for m, r in tr.iterrows()]

    # ---------- 03 · what to do ----------
    sets = buyers.groupby("cat").customer_key.apply(set).to_dict()
    cross = []
    for a in cats:
        for bb in cats:
            A, B = sets.get(a, set()), sets.get(bb, set())
            if A and a != bb:
                p_ab, p_b = len(A & B) / len(A), len(B) / n_items
                cross.append({"a": a, "b": bb, "share": p_ab, "lift": p_ab / p_b if p_b else None, "both": len(A & B), "not_yet": len(A - B)})
    flows = {}
    ak = keys.intersection(ENTRY.index)
    for a in cats:
        ks = ak[ENTRY.reindex(ak).to_numpy() == a]
        for nxt, n in SECOND.reindex(ks).fillna("No 2nd purchase yet").value_counts().items():
            flows[(a, nxt)] = int(n)
    sankey = [{"source": a, "target": (bb + " ·2nd" if bb != "No 2nd purchase yet" else bb), "value": v} for (a, bb), v in flows.items()]
    nxt_rows = []
    for a in cats:
        cand = [x for x in cross if x["a"] == a and x["b"] != "Other" and x["lift"]]
        if not cand:
            continue
        best = max(cand, key=lambda x: x["lift"])
        common = sorted([(v, bb) for (aa, bb), v in flows.items() if aa == a and bb not in ("No 2nd purchase yet", a)], reverse=True)
        nxt_rows.append({"category": a, "next": best["b"], "share": best["share"], "lift": best["lift"], "not_yet": best["not_yet"],
                         "common_second": common[0][1] if common else None})
    # audiences: valuable segments that never bought a category but buy categories that go with it
    lift = {(x["a"], x["b"]): x["lift"] for x in cross}
    bought = buyers.groupby("customer_key").cat.apply(set)
    aud = []
    for seg in ["Champions", "Loyal High Value", "Potential Loyalists", "New Customers", "At Risk High Value"]:
        bk = bought.reindex(sc.index[sc.segment == seg]).dropna()
        for cat in cats:
            if cat == "Other":
                continue
            cand = bk[bk.apply(lambda s: cat not in s)]
            if cand.empty:
                continue
            aff = cand.apply(lambda s: max((lift.get((x, cat)) or 0) for x in s))
            sel = aff[aff >= 1.0].index
            if len(sel) >= SMALL:
                aud.append({"segment": seg, "category": cat, "customers": int(len(sel)),
                            "consented": int(sc.marketing_consent.reindex(sel).fillna(False).astype(bool).sum()),
                            "affinity": float(aff.reindex(sel).mean()), "order_value": K[cat]["aov"]})
    aud.sort(key=lambda a: -(a["customers"] * a["order_value"] * a["affinity"]))
    crm = []
    for s in order:
        row = {"segment": s}
        for cat in cats:
            if cat in CAT_SEG:
                d = CAT_SEG[cat]
                d = d[d.index.isin(keys) & (d.segment == s)]
                row[cat] = int(len(d)); row[cat + "|consent"] = int(d.consent.sum())
        crm.append(row)

    # ---------- moves, ranked by revenue in play (5% conversion assumption) ----------
    plays = []
    if len(br) >= 2:
        one, two = br[0], br[1]
        single = ncat[ncat == 1].index
        active = single[sc.R.reindex(single).to_numpy() >= 3]
        cons = int(sc.marketing_consent.reindex(active).fillna(False).astype(bool).sum())
        recs = ", ".join(f"{e(r['category'])} → {e(r['next'])}" for r in nxt_rows[:3])
        plays.append({"kicker": "Grow breadth", "metric": f"{two['revenue_per_customer'] / one['revenue_per_customer']:.1f}×", "metric_label": "value of a 2-category vs 1-category customer",
                      "title": "Turn one-category buyers into two-category buyers",
                      "why": f"{one['share']:.0%} of customers buy in one category only. Two-category customers spend {money(two['revenue_per_customer'], CUR)} vs {money(one['revenue_per_customer'], CUR)} and {two['repeat_rate']:.0%} buy again (vs {one['repeat_rate']:.0%}).",
                      "who": f"{len(active):,} one-category customers active recently (R 3–5) · {cons:,} with consent",
                      "how": f"Post-purchase and browse flows that recommend the next category: {recs}.",
                      "measure": "Share buying a second category within 90 days", "value": len(active) * 0.05 * (two["revenue_per_customer"] - one["revenue_per_customer"])})
    gr = [r for r in rows if r["growth"] is not None and r["name"] != "Other"]
    if gr:
        worst, best = min(gr, key=lambda r: r["growth"]), max(gr, key=lambda r: r["growth"])
        if worst["growth"] < 0:
            reach = (worst["buyers_growth"] or 0) < (worst["rpb_growth"] or 0)
            plays.append({"kicker": "Fix the decline", "metric": pctf(worst["growth"], True), "metric_label": f"{e(worst['name'])} revenue, last {HALF} vs previous {HALF} months",
                          "title": f"Stop the decline in {e(worst['name'])}",
                          "why": f"Buyers {pctf(worst['buyers_growth'], True)}, revenue per buyer {pctf(worst['rpb_growth'], True)}: this is a {'reach problem — fewer people buy' if reach else 'value problem — buyers spend less'}.",
                          "who": f"{int(worst['customers'] * (worst['lapsing'] or 0)):,} {e(worst['name'])} buyers who have not bought recently (R 1–2)",
                          "how": "Reactivation and prospecting with category creative" if reach else "Bundles, assortment and price review; upsell in the category",
                          "measure": "Monthly buyers and revenue per buyer in the category", "value": 0.05 * abs(worst["growth"]) * worst["revenue"] / 2})
        else:
            plays.append({"kicker": "Back the winner", "metric": pctf(best["growth"], True), "metric_label": f"{e(best['name'])} revenue, last {HALF} vs previous {HALF} months",
                          "title": f"Lean into {e(best['name'])}", "why": f"Fastest-growing category: buyers {pctf(best['buyers_growth'], True)}, revenue per buyer {pctf(best['rpb_growth'], True)}.",
                          "who": f"{best['customers']:,} {e(best['name'])} buyers", "how": "Shift prospecting budget and lead creative with the category",
                          "measure": "Category share of new-customer revenue", "value": 0.05 * best["growth"] * best["revenue"] / 2})
    top = rows[0] if rows else None
    if top and top["name"] in CAT_SEG:
        d = CAT_SEG[top["name"]]
        d = d[d.index.isin(keys)]
        hv = d[(d.R <= 2) & (d.M >= 4)]
        if len(hv) >= SMALL:
            plays.append({"kicker": "Win back", "metric": money(hv.monetary.sum(), CUR), "metric_label": f"spent on {e(top['name'])} by these customers in the window",
                          "title": f"Win back high-value {e(top['name'])} buyers",
                          "why": f"{e(top['name'])} is the largest category ({top['revenue_share']:.0%} of revenue). These customers were its top spenders but have not bought it for a long time.",
                          "who": f"{len(hv):,} customers with high {e(top['name'])} spend and low {e(top['name'])} recency · {int(hv.consent.sum()):,} with consent",
                          "how": "Personal win-back with new arrivals in the category; exclude them from generic discounts",
                          "measure": "Share buying the category again within 60 days", "value": 0.05 * hv.monetary.sum()})
    cy_ok = [x for x in cycle if x["median_days"] and x["back_90"] is not None]
    if cy_ok:
        f = max(cy_ok, key=lambda x: x["back_90"])
        last = (snap - b[b.cat == f["category"]].groupby("customer_key").transaction_date.max()).dt.days
        due = last[(last >= 0.7 * f["median_days"]) & (last <= 1.3 * f["median_days"])]
        plays.append({"kicker": "Time it right", "metric": f"Day {f['median_days']:.0f}", "metric_label": f"typical gap between {e(f['category'])} purchases",
                      "title": f"Remind {e(f['category'])} buyers when they are due",
                      "why": f"{f['back_90']:.0%} of {e(f['category'])} buyers come back to it within 90 days — the fastest return of any category.",
                      "who": f"{len(due):,} buyers are due now (last {e(f['category'])} purchase {0.7 * f['median_days']:.0f}–{1.3 * f['median_days']:.0f} days ago)",
                      "how": "Triggered reminder around the typical gap; Customer Match audience refreshed weekly",
                      "measure": "Repurchase within 90 days vs a holdout", "value": len(due) * 0.05 * (K[f["category"]]["aov"] or 0)})
    rr = [r for r in rows if r["return_rate"] is not None]
    if rr:
        avg = sum(r["return_rate"] * r["revenue"] for r in rr) / sum(r["revenue"] for r in rr)
        w = max(rr, key=lambda r: r["return_rate"])
        if avg > 0 and w["return_rate"] >= 1.15 * avg:
            plays.append({"kicker": "Protect margin", "metric": f"{w['return_rate']:.1%}", "metric_label": f"{e(w['name'])} return rate (average {avg:.1%})",
                          "title": f"Bring {e(w['name'])} returns down", "why": f"{e(w['name'])} returns are {w['return_rate'] / avg:.1f}× the average category.",
                          "who": "Sub-categories with the highest return rate (see the scoreboard)", "how": "Size guidance, product content and reviews on the sub-categories driving returns",
                          "measure": "Return rate by sub-category", "value": 0.05 * (w["return_rate"] - avg) * w["revenue"]})
    if aud:
        a = aud[0]
        plays.append({"kicker": "Cross-sell", "metric": f"{a['customers']:,}", "metric_label": f"{e(a['segment'])} who never bought {e(a['category'])}",
                      "title": f"Introduce {e(a['category'])} to {e(a['segment'])}",
                      "why": f"They buy categories that go together with {e(a['category'])} ({a['affinity']:.2f}× affinity) but have never bought it.",
                      "who": f"{a['customers']:,} customers · {a['consented']:,} with consent", "how": "Email and Customer Match with category inspiration; first-order incentive only for the Potential Loyalists",
                      "measure": "First purchase in the category vs a holdout", "value": a["customers"] * 0.05 * (a["order_value"] or 0)})
    plays.sort(key=lambda p: -p["value"])

    # ---------- chapter headlines ----------
    s1 = None
    if rows:
        cross_rev = sc.monetary.reindex(ncat[ncat >= 2].index).sum() / sc.monetary.reindex(ncat.index).sum()
        s1 = (f"{e(rows[0]['name'])} carries {rows[0]['revenue_share']:.0%} of revenue — but the value sits in breadth: "
              f"the {(ncat >= 2).mean():.0%} of customers who buy in two or more categories bring {cross_rev:.0%} of it.")
    s2 = None
    if gr and len(gr) > 1:
        lap = max((r for r in rows if r["lapsing"] is not None), key=lambda r: r["lapsing"])
        s2 = (f"Over the last {HALF} months {e(best['name'])} grew {pctf(best['growth'], True)} while {e(worst['name'])} "
              f"{'shrank' if worst['growth'] < 0 else 'grew only'} {pctf(worst['growth'], True)}. "
              f"{e(lap['name'])} has the most buyers slipping away: {lap['lapsing']:.0%} have not bought recently.")
    s3 = f"{len(plays)} moves, ranked by the revenue they put in play." if plays else None

    return {"summary": {"customers_with_items": int(n_items), "coverage": float(n_items / len(keys)),
                        "avg_categories": float(ncat.mean()) if len(ncat) else None, "cross_share": float((ncat >= 2).mean()) if len(ncat) else None,
                        "multi_share": float((ncat >= 3).mean()) if len(ncat) else None,
                        "cross_revenue_share": float(sc.monetary.reindex(ncat[ncat >= 2].index).sum() / sc.monetary.reindex(ncat.index).sum()) if len(ncat) else None},
            "headlines": {"situation": s1, "complication": s2, "resolution": s3},
            "categories": cats, "kpis": rows, "breadth": br, "shopper_types": st_rows, "segment_mix": seg_mix, "cycle": cycle, "trend": trend,
            "cross": cross, "next": nxt_rows, "sankey": sankey, "audiences": aud[:12], "crm": crm, "plays": plays[:5]}


t0 = time.time()
opts = res.report["filters"]
cat_buyers = {cat: set(cc[(cc.cat == cat) & (cc.orders > 0)].customer_key) for cat in named}
views = {}
for m_, c_, l_, k_ in itertools.product(["all"] + [o["value"] for o in opts["market"]], ["all"] + [o["value"] for o in opts["channel"]],
                                        ["all"] + [o["value"] for o in opts["loyalty"]], ["all"] + named):
    mask = np.ones(len(scored), bool)
    if m_ != "all": mask &= (scored.market == m_).to_numpy()
    if c_ != "all": mask &= (scored.channel == c_).to_numpy()
    if l_ != "all": mask &= (scored.loyalty == l_).to_numpy()
    if k_ != "all": mask &= scored.index.isin(cat_buyers[k_])
    if mask.sum():
        views[f"{m_}|{c_}|{l_}|{k_}"] = view(scored[mask])
print("views", len(views), "seconds", round(time.time() - t0, 1), file=sys.stderr)
report = res.report
report["categories"] = jsonable({"meta": {"basis": "revenue", "dominance": DOMINANCE, "max_named": MAX_NAMED, "small": SMALL, "half_months": HALF,
                                          "item_vs_transaction_gap": float(item_vs_tx), "categories": CATS, "subs": int(li.item_sub_category.nunique())},
                                 "views": views}, digits=4)
report["filters"]["category"] = [{"value": cat, "label": cat, "customers": len(cat_buyers[cat])} for cat in named]
out = Path(sys.argv[2])
out.parent.mkdir(parents=True, exist_ok=True)
json.dump(report, open(out, "w"), separators=(",", ":"))
print("MB", round(out.stat().st_size / 1e6, 2), file=sys.stderr)
