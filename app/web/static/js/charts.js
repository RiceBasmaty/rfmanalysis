/* RFM Studio — report cards registry.

   HOW TO ADD A VISUALISATION
   1. Make sure the data is in the report JSON (rfm_engine/views.py or cohorts.py builds it per filter view).
   2. Add an entry to RFM.CARDS below:
        "my-card": {
          title: "Title", sub: (ctx) => "Subtitle", how: "How to read it (optional)",
          when: (ctx) => true,                       // optional: hide when data is missing
          chart: (ctx) => ({ ...echarts option }),    // or html: (ctx) => "<div>…</div>"
          table: (ctx) => ({ cols, rows }),           // optional: "View as table" for the same data
          controls: [{ key: "x", options: [["a", "A"], ["b", "B"]] }],  // optional toggle, value in ctx.state.x
          height: 340,
        }
   3. Put its id in RFM.LAYOUT under a tab: a row with one id is full width, two ids sit side by side.

   ctx = { R: whole report, view: current filter view, mk: markets section for the filters, state, cur, order }
*/
(function (global) {
  "use strict";
  const RFM = global.RFM;
  const { T, SERIES, fmt, pct, esc, money, compact, base, axis, shadow, stat, trend } = RFM;

  const segRows = (ctx) => ctx.view.segments.kpis.filter((r) => r.segment !== "Total" && r.customers > 0);
  const total = (ctx) => ctx.view.segments.kpis.find((r) => r.segment === "Total");
  const marketColor = (ctx, m) => { const i = ctx.R.filters.market.findIndex((o) => o.value === m); return i >= 0 && i < SERIES.length ? SERIES[i] : T.muted; };
  const heatLabel = (data, idx) => { const mx = Math.max(...data.map((d) => d[idx] || 0)); return (d) => ({ value: d, label: RFM.cellLabel(d[idx] || 0, mx) }); };
  const monthTick = (labels) => (v) => (v.slice(5) === "01" || v === labels[0] ? v : v.slice(5));
  // Shopper types: single-category types by size first, then Cross-shopper and Multi-category shopper.
  const shopperOrder = (rows) => {
    const mixed = ["Cross-shopper", "Multi-category shopper"];
    return [...rows.filter((r) => !mixed.includes(r.type)).sort((a, b) => b.customers - a.customers), ...mixed.map((m) => rows.find((r) => r.type === m)).filter(Boolean)];
  };
  const NEG = "#FF4B50";   // Precis "negative"
  const signed = (v) => (v == null ? "–" : (v > 0 ? "+" : "") + pct(v));
  const growthCell = (v) => (v == null ? "–" : `<span class="${v >= 0 ? "up" : "down"}">${v >= 0 ? "▲" : "▼"} ${pct(Math.abs(v))}</span>`);
  const chapter = (n, eyebrow, headline) => `<div class="chapter"><div class="chapter-n">${n}</div><div><div class="eyebrow">${eyebrow}</div><h2>${headline || ""}</h2></div></div>`;
  // Stacked segment mix (100% or customers) for any grouping; the hovered segment is highlighted in the tooltip.
  const stackMix = (ctx, groups, cells, gk, mode) => {
    const segs = ctx.order, cell = {}, count = mode === "count";
    cells.forEach((s) => (cell[s[gk] + "|" + s.segment] = s));
    const totals = groups.map((g) => segs.reduce((a, s) => a + ((cell[g + "|" + s] || {}).customers || 0), 0)), maxT = Math.max(...totals, 1);
    return { ...base(), grid: { left: 8, right: 8, top: 64, bottom: 8, containLabel: true }, legend: { ...base().legend, data: segs },
      tooltip: { ...base().tooltip, trigger: "axis", axisPointer: shadow, formatter: (ps) => `<b>${esc(ps[0].axisValue)}</b>` + ps.slice().reverse().map((p) => {
        const c = cell[p.axisValue + "|" + p.seriesName] || {}, line = `${p.marker}${esc(p.seriesName)}: <b>${pct(c.share || 0, 1)}</b> <span style="color:${T.muted}">(${fmt(c.customers || 0)})</span>`;
        return ctx.state.hoverSeries === p.seriesName ? `<div style="font-weight:700;background:${T.lp};margin:1px -6px;padding:1px 6px;border-radius:6px">${line}</div>` : `<div style="opacity:${ctx.state.hoverSeries ? 0.6 : 1}">${line}</div>`;
      }).join("") },
      xAxis: axis({ type: "category", data: groups, axisLabel: { color: T.navy, fontWeight: 600, fontSize: 12.5 } }),
      yAxis: axis(count ? { type: "value", axisLabel: { color: T.muted, formatter: (v) => compact(v) } } : { type: "value", min: 0, max: 1, axisLabel: { color: T.muted, formatter: (v) => pct(v) } }),
      series: segs.map((seg, i) => ({ name: seg, type: "bar", stack: "mix", barMaxWidth: 64, color: RFM.segmentColor(i), itemStyle: { borderColor: "#fff", borderWidth: 1 },
        data: groups.map((g) => (cell[g + "|" + seg] || {})[count ? "customers" : "share"] || 0), emphasis: { focus: "series" },
        label: { show: true, position: "inside", color: RFM.segmentInk(i), fontSize: 11, fontWeight: 600,
          formatter: (p) => (count ? (p.value / maxT >= 0.05 ? compact(p.value) : "") : p.value >= 0.04 ? pct(p.value) : "") } })) };
  };
  const hoverSeries = (inst, ctx) => {
    inst.on("mouseover", (p) => { ctx.state.hoverSeries = p.seriesName; });
    inst.on("mouseout", () => { ctx.state.hoverSeries = null; });
    inst.on("globalout", () => { ctx.state.hoverSeries = null; });
  };
  const basisText = (ctx) => (ctx.view.cohorts.basis === "first_ever" ? "first-ever purchase" : "first purchase in the window");

  const CARDS = {
    /* ================= OVERVIEW ================= */
    "ov-stats": {
      plain: true,
      html: (ctx) => {
        const h = ctx.view.overview.headline;
        return `<div class="grid g4">${[
          stat("Customers scored", fmt(h.customers), `last ${ctx.R.meta.lookback_months} months`, "lp"),
          stat("Net revenue", money(h.net_revenue, ctx.cur), "after returns", "hl"),
          stat("Revenue per customer", money(h.revenue_per_customer, ctx.cur), `${fmt(h.avg_purchases, 1)} purchases on average`),
          stat("Repeat rate", pct(h.repeat_rate), `bought 2+ times · return rate ${pct(h.return_rate, 1)}`, "lt"),
        ].join("")}</div>`;
      },
    },
    "ov-insights": {
      title: "Key insights", sub: () => "Generated from the numbers. Sense-check before sharing.",
      html: (ctx) => `<div class="insights">${ctx.view.overview.insights.map((t) => `<div class="insight"><div>${t}</div></div>`).join("")}</div>`,
    },
    "ov-share": {
      title: "Share of customers vs share of revenue", sub: () => "By segment", height: 420,
      how: "A revenue bar much longer than the customer bar marks a high-value segment — protect it. A long customer bar with a short revenue bar is a large, low-value group: reach it with cheap channels or suppress it.",
      chart: (ctx) => {
        const rows = segRows(ctx).slice().reverse();
        return { ...base(), grid: { left: 8, right: 44, top: 40, bottom: 8, containLabel: true },
          tooltip: { ...base().tooltip, trigger: "axis", axisPointer: shadow, valueFormatter: (v) => pct(v, 1) },
          xAxis: axis({ type: "value", axisLabel: { color: T.muted, formatter: (v) => pct(v) } }),
          yAxis: axis({ type: "category", data: rows.map((r) => r.segment), axisLine: { show: false }, axisLabel: { color: T.navy, fontSize: 12.5 } }),
          series: [
            { name: "Share of customers", type: "bar", data: rows.map((r) => r.customer_share), barWidth: 10, barGap: "25%", itemStyle: { color: T.sky, borderRadius: [0, 4, 4, 0] },
              label: { show: true, position: "right", color: T.ink2, fontSize: 11, formatter: (p) => pct(p.value) } },
            { name: "Share of revenue", type: "bar", data: rows.map((r) => r.revenue_share), barWidth: 10, itemStyle: { color: T.blue, borderRadius: [0, 4, 4, 0] },
              label: { show: true, position: "right", color: T.navy, fontWeight: 600, fontSize: 11, formatter: (p) => pct(p.value) } },
          ] };
      },
      table: (ctx) => ({ cols: [{ key: "segment", label: "Segment" }, { key: "customer_share", label: "Share of customers", num: 1, fmt: (v) => pct(v, 1) },
        { key: "revenue_share", label: "Share of revenue", num: 1, fmt: (v) => pct(v, 1) }], rows: segRows(ctx) }),
    },
    "ov-conc": {
      title: "Revenue concentration", sub: (ctx) => {
        const c = ctx.view.overview.concentration;
        return c.top20 == null ? "Customers ranked by net revenue" : `Top 20% of customers bring ${pct(c.top20)} of revenue; half comes from the top ${pct(c.half)}`;
      },
      how: "Customers are ranked from highest to lowest net revenue. The steeper the curve, the more the business depends on a few customers. Above ~70% for the top 20% means retaining the top tier matters more than acquisition.",
      when: (ctx) => ctx.view.overview.concentration.curve.length > 0,
      chart: (ctx) => {
        const c = ctx.view.overview.concentration;
        return { ...base(), legend: { show: false }, grid: { left: 8, right: 24, top: 24, bottom: 40, containLabel: true },
          tooltip: { ...base().tooltip, trigger: "axis", formatter: (p) => `Top ${pct(p[0].value[0])} of customers → <b>${pct(p[0].value[1])}</b> of revenue` },
          xAxis: axis({ type: "value", min: 0, max: 1, axisLabel: { color: T.muted, formatter: (v) => pct(v) }, name: "Share of customers (ranked by net revenue)", nameLocation: "middle", nameGap: 28, nameTextStyle: { color: T.muted } }),
          yAxis: axis({ type: "value", min: 0, max: 1, axisLabel: { color: T.muted, formatter: (v) => pct(v) } }),
          series: [{ type: "line", data: c.curve, symbol: "none", color: T.blue, lineStyle: { width: 2 }, areaStyle: { color: "rgba(40,70,220,.08)" },
            markPoint: { symbol: "circle", symbolSize: 10, itemStyle: { color: T.blue, borderColor: "#fff", borderWidth: 2 }, data: [{ coord: [0.2, c.top20] }],
              label: { show: true, position: "right", color: T.navy, fontWeight: 600, formatter: `Top 20% → ${pct(c.top20)}` } } }] };
      },
      table: (ctx) => ({ cols: [{ key: "x", label: "Top share of customers", num: 1, fmt: (v) => pct(v) }, { key: "y", label: "Share of revenue", num: 1, fmt: (v) => pct(v, 1) }],
        rows: ctx.view.overview.concentration.curve.filter((_, i) => i % 10 === 0 && i > 0).map(([x, y]) => ({ x, y })) }),
    },
    "ov-data": {
      title: "About this data", sub: () => "From the data check",
      html: (ctx) => {
        const d = ctx.R.data, s = d.stats, cov = d.coverage;
        const rows = [
          ["Transactions received", fmt(s.rows_received)],
          ["Used after cleaning and trims", `${fmt(s.rows_identified)} · ${pct(s.rows_identified / s.rows_received, 1)}`],
          ["Purchases tied to a known customer", pct(cov["Purchases identified"])],
          ...(cov["Retail purchases linked"] != null ? [["Retail purchases tied to a customer", pct(cov["Retail purchases linked"])]] : []),
          ["Outliers (possible B2B)", `${fmt(s.outliers)} · ${pct(s.outlier_revenue_share, 1)} of revenue · ${ctx.R.meta.outlier_action === "exclude" ? "excluded" : "kept"}`],
          ["Trims applied", d.trim_log.length ? d.trim_log.map((t) => `${t.trim} (−${fmt(t.removed)})`).join(" · ") : "none"],
          ["Cut-offs", ctx.R.meta.thresholds_frozen ? `frozen from ${ctx.R.meta.baseline_label || "a previous run"}` : "calculated from this data"],
        ];
        const lvl = d.verdict.level, kind = lvl === "critical" ? "crit" : lvl === "warning" ? "warn" : "ok";
        return `<div class="row">${RFM.pill(kind, lvl === "ok" ? "clean" : `${d.counts.critical} critical · ${d.counts.warning} warnings`)}</div>
          <div class="table-wrap"><table class="t"><tbody>${rows.map(([k, v]) => `<tr><td>${esc(k)}</td><td class="n">${esc(v)}</td></tr>`).join("")}</tbody></table></div>
          ${ctx.R.warnings.map((w) => `<p class="small muted">${esc(w)}</p>`).join("")}`;
      },
    },

    /* ================= SEGMENTS ================= */
    "seg-kpis": {
      title: "Segment KPIs", sub: (ctx) => `One row per segment · amounts in ${ctx.cur}, net of returns`,
      how: "Compare customer share with revenue share: a segment with a much bigger revenue share than customer share is where value is concentrated. Consent share shows how many you can reach with email, Customer Match or Meta audiences.",
      html: (ctx) => {
        const rows = ctx.view.segments.kpis.filter((r) => r.customers > 0).map((r) => ({ ...r, _total: r.segment === "Total" }));
        const max = Math.max(...rows.filter((r) => !r._total).map((r) => r.revenue_share || 0));
        const cols = [
          { key: "segment", label: "Segment", html: 1, fmt: (v, r) => (r._total ? "Total" : `<b>${esc(v)}</b>`) },
          { key: "customers", label: "Customers", num: 1, fmt: (v) => fmt(v) },
          { key: "customer_share", label: "Customer share", num: 1, fmt: (v) => pct(v, 1) },
          { key: "revenue_share", label: "Revenue share", num: 1, html: 1, fmt: (v, r) => (r._total ? "100%" : `<div class="bar-cell"><i style="width:${(70 * (v || 0)) / (max || 1)}px"></i>${pct(v, 1)}</div>`) },
          { key: "net_revenue", label: "Net revenue", num: 1, fmt: (v) => compact(v) },
          { key: "revenue_per_customer", label: "Revenue / customer", num: 1, fmt: (v) => fmt(v) },
          { key: "aov", label: "AOV", num: 1, fmt: (v) => fmt(v) },
          { key: "avg_purchases", label: "Avg purchases", num: 1, fmt: (v) => fmt(v, 1) },
          { key: "median_days_since_purchase", label: "Median days since", num: 1, fmt: (v) => fmt(v) },
          { key: "return_rate", label: "Return rate", num: 1, fmt: (v) => pct(v, 1) },
          ...(ctx.R.meta.has.consent ? [{ key: "consent_share", label: "Consent", num: 1, fmt: (v) => pct(v) }] : []),
        ];
        return RFM.table(cols, rows);
      },
    },
    "seg-rf": {
      title: "Customers by Recency and Frequency", sub: () => "Hover a cell for revenue per customer",
      how: "Right = bought recently, top = buys often. The top-right corner is the loyal, active core. The bottom row is one-time buyers — turning them into second-time buyers is usually the cheapest growth lever. Top-left cells are valuable customers drifting away.",
      chart: (ctx) => {
        const g = ctx.view.segments.rf_grid, data = g.map((c) => [c.R - 1, 5 - c.F, c.customers, c.revenue_per_customer]);
        const mx = Math.max(...data.map((d) => d[2]), 1);
        return { ...base(), grid: { left: 8, right: 8, top: 8, bottom: 40, containLabel: true },
          tooltip: { ...base().tooltip, formatter: (p) => `R${p.value[0] + 1} · F${5 - p.value[1]}<br>Customers: <b>${fmt(p.value[2])}</b><br>Revenue / customer: <b>${fmt(p.value[3])} ${ctx.cur}</b>` },
          xAxis: axis({ type: "category", data: ["R1", "R2", "R3", "R4", "R5"], name: "Recency score (5 = most recent)", nameLocation: "middle", nameGap: 26, nameTextStyle: { color: T.muted } }),
          yAxis: axis({ type: "category", data: ["F5", "F4", "F3", "F2", "F1"], axisLine: { show: false } }),
          visualMap: { show: false, min: 0, max: mx, dimension: 2, inRange: { color: T.seq } },
          series: [{ type: "heatmap", data: data.map(heatLabel(data, 2)), itemStyle: { borderColor: "#fff", borderWidth: 3, borderRadius: 6 },
            label: { show: true, fontSize: 11.5, formatter: (p) => fmt(p.value[2]) }, emphasis: { itemStyle: { borderColor: T.navy, borderWidth: 2 } } }] };
      },
      table: (ctx) => ({ cols: [{ key: "F", label: "F score" }, { key: "R", label: "R score" }, { key: "customers", label: "Customers", num: 1, fmt: (v) => fmt(v) },
        { key: "revenue_per_customer", label: "Revenue / customer", num: 1, fmt: (v) => fmt(v) }], rows: ctx.view.segments.rf_grid.slice().sort((a, b) => b.F - a.F || b.R - a.R) }),
    },
    "seg-map": {
      title: "Segment map", sub: () => "Bubble size = net revenue",
      how: "Right = bought recently, up = buys often. Healthy, valuable segments sit top-right. Big bubbles drifting left are revenue at risk. A good single slide to explain the segmentation to a client.",
      chart: (ctx) => {
        const rows = segRows(ctx), mx = Math.max(...rows.map((r) => Math.max(r.net_revenue, 0)), 1);
        return { ...base(), grid: { left: 8, right: 24, top: 24, bottom: 40, containLabel: true },
          tooltip: { ...base().tooltip, formatter: (p) => { const r = rows[p.dataIndex]; return `<b>${esc(r.segment)}</b><br>Median days since purchase: ${fmt(r.median_days_since_purchase)}<br>Avg purchases: ${fmt(r.avg_purchases, 1)}<br>Customers: ${fmt(r.customers)}<br>Revenue share: ${pct(r.revenue_share, 1)}`; } },
          xAxis: axis({ type: "value", inverse: true, name: "Median days since last purchase (recent →)", nameLocation: "middle", nameGap: 28, nameTextStyle: { color: T.muted } }),
          yAxis: axis({ type: "value", name: "Avg purchases", nameTextStyle: { color: T.muted, align: "left" } }),
          series: [{ type: "scatter", data: rows.map((r) => [r.median_days_since_purchase, r.avg_purchases, Math.max(r.net_revenue, 0)]),
            symbolSize: (v) => 12 + Math.sqrt(v[2] / mx) * 60, itemStyle: { color: "rgba(40,70,220,.78)", borderColor: "#fff", borderWidth: 2 },
            label: { show: true, formatter: (p) => rows[p.dataIndex].segment, position: "top", color: T.navy, fontSize: 11.5, fontWeight: 600 }, labelLayout: { hideOverlap: true } }] };
      },
    },
    "seg-mix": {
      title: "Channel mix by segment", sub: () => "Share of customers buying online only, in store only, or both",
      how: "Customers who buy in both channels are usually the most valuable. A segment with a large store-only share is hard to reach with online media — check consent and loyalty IDs.",
      when: (ctx) => ctx.R.meta.has.medium && (ctx.view.segments.channel_mix || []).length > 0,
      chart: (ctx) => {
        const m = ctx.view.segments.channel_mix.slice().reverse();
        return { ...base(), grid: { left: 8, right: 16, top: 40, bottom: 8, containLabel: true },
          tooltip: { ...base().tooltip, trigger: "axis", axisPointer: shadow, valueFormatter: (v) => pct(v) },
          xAxis: axis({ type: "value", max: 1, axisLabel: { color: T.muted, formatter: (v) => pct(v) } }),
          yAxis: axis({ type: "category", data: m.map((r) => r.segment), axisLine: { show: false }, axisLabel: { color: T.navy, fontSize: 12 } }),
          series: [["online", "Online only"], ["store", "Store only"], ["both", "Both"]].map(([k, n], j) => ({ name: n, type: "bar", stack: "m", barWidth: 16, color: SERIES[j],
            data: m.map((r) => r[k]), itemStyle: { borderColor: "#fff", borderWidth: 2, borderRadius: j === 2 ? [0, 4, 4, 0] : 0 } })) };
      },
      table: (ctx) => ({ cols: [{ key: "segment", label: "Segment" }, { key: "online", label: "Online only", num: 1, fmt: (v) => pct(v) }, { key: "store", label: "Store only", num: 1, fmt: (v) => pct(v) }, { key: "both", label: "Both", num: 1, fmt: (v) => pct(v) }], rows: ctx.view.segments.channel_mix }),
    },
    "seg-cutoffs": {
      title: "How the scores are cut", sub: (ctx) => (ctx.R.meta.thresholds_frozen ? `Frozen from ${ctx.R.meta.baseline_label || "a previous run"}` : "Calculated from this data — R and M are quintiles, F uses fixed bins"),
      html: (ctx) => RFM.table([{ key: "score", label: "Score", num: 1 }, { key: "recency_days", label: "R — days since last purchase", num: 1 },
        { key: "purchases", label: "F — purchases", num: 1 }, { key: "net_revenue", label: `M — net revenue (${ctx.cur})`, num: 1 }], ctx.R.threshold_table),
    },
    "seg-actions": {
      title: "Segments and suggested actions", sub: () => "Rules are checked top to bottom; the first match wins",
      html: (ctx) => {
        const k = Object.fromEntries(ctx.view.segments.kpis.map((r) => [r.segment, r]));
        return `<div class="seg-cards">${ctx.R.segments.definitions.map((d) => {
          const r = k[d.segment] || {};
          return `<div class="seg-card"><div class="nm">${esc(d.segment)}${RFM.pill("info", pct(r.customer_share || 0))}</div>
            <div class="row"><span><b>${pct(r.revenue_share || 0)}</b> revenue</span><span><b>${fmt(r.customers || 0)}</b> customers</span></div>
            <div class="small muted"><code>${esc(d.rule)}</code></div>
            ${d.description ? `<div class="act">${esc(d.description)}</div>` : ""}${d.suggested_action ? `<div class="act"><b>Action:</b> ${esc(d.suggested_action)}</div>` : ""}</div>`;
        }).join("")}</div>`;
      },
    },

    /* ================= COHORTS ================= */
    "coh-stats": {
      plain: true,
      html: (ctx) => {
        const s = ctx.view.cohorts.summary, unit = ctx.view.cohorts.grain === "quarter" ? "Quarter" : "Month";
        return `<div class="grid g4">${[
          stat("New customers acquired", fmt(s.acquired), `${basisText(ctx)} in the window`, "lp"),
          stat("Came back within 90 days", pct(s.back_90), "made a 2nd purchase", "hl"),
          stat(`${unit}-${s.retention_period || "–"} retention`, pct(s.retention_last, 1), "customer-weighted across cohorts"),
          stat("Median time to 2nd order", s.median_days_to_second == null ? "–" : `${fmt(s.median_days_to_second)} days`, "for those who returned", "lt"),
        ].join("")}</div>`;
      },
    },
    "coh-heat": {
      title: "Retention by acquisition cohort", height: 460,
      sub: (ctx) => (ctx.state.cohMeasure === "revenue" ? "Net revenue in each period as a share of the cohort's first-period revenue" : `Share of each cohort that bought again N ${ctx.view.cohorts.grain}s after their ${basisText(ctx)}`),
      controls: [{ key: "cohMeasure", options: [["customers", "Customers"], ["revenue", "Revenue"]] }],
      how: "Each row is the group of customers acquired in that period. Read across to see how many came back. Compare a column top to bottom: darker lower rows mean newer customers return more often than older ones did. Only complete periods are shown.",
      when: (ctx) => ctx.view.cohorts.rows.length > 0,
      chart: (ctx) => {
        const c = ctx.view.cohorts, key = ctx.state.cohMeasure === "revenue" ? "revenue_retention" : "retention", p = c.grain === "quarter" ? "Q" : "M";
        const data = [];
        c.rows.forEach((r, ri) => r[key].forEach((v, k) => { if (v != null) data.push([k, ri, +(v * 100).toFixed(1), r.customers]); }));
        const mx = Math.max(...data.map((d) => d[2]), 1);
        return { ...base(), grid: { left: 8, right: 8, top: 8, bottom: 40, containLabel: true },
          tooltip: { ...base().tooltip, formatter: (q) => `Cohort <b>${esc(c.rows[q.value[1]].cohort)}</b> · ${p}${q.value[0] + 1}<br><b>${q.value[2]}%</b> ${ctx.state.cohMeasure === "revenue" ? "of first-period revenue" : `of ${fmt(q.value[3])} customers bought again`}` },
          xAxis: axis({ type: "category", data: Array.from({ length: c.periods }, (_, i) => p + (i + 1)), name: `${c.grain === "quarter" ? "Quarters" : "Months"} after acquisition`, nameLocation: "middle", nameGap: 26, nameTextStyle: { color: T.muted } }),
          yAxis: axis({ type: "category", data: c.rows.map((r) => r.cohort), inverse: true, axisLine: { show: false } }),
          visualMap: { show: false, min: 0, max: mx, dimension: 2, inRange: { color: T.seq } },
          series: [{ type: "heatmap", data: data.map(heatLabel(data, 2)), itemStyle: { borderColor: "#fff", borderWidth: 2, borderRadius: 4 },
            label: { show: c.periods <= 18, fontSize: 10.5, formatter: (q) => q.value[2].toFixed(0) } }] };
      },
      table: (ctx) => {
        const c = ctx.view.cohorts, key = ctx.state.cohMeasure === "revenue" ? "revenue_retention" : "retention", p = c.grain === "quarter" ? "Q" : "M";
        return { cols: [{ key: "cohort", label: "Cohort" }, { key: "customers", label: "Customers", num: 1, fmt: (v) => fmt(v) },
          ...Array.from({ length: c.periods }, (_, i) => ({ key: "p" + i, label: p + (i + 1), num: 1, fmt: (v) => pct(v, 1) }))],
          rows: c.rows.map((r) => ({ cohort: r.cohort, customers: r.customers, ...Object.fromEntries(r[key].map((v, i) => ["p" + i, v])) })) };
      },
    },
    "coh-ltv": {
      title: "Revenue per customer over time", sub: () => "Cumulative net revenue per acquired customer, by quarterly cohort",
      how: "Each line follows one quarter's new customers. A line that keeps climbing means those customers keep buying. Lines above older ones mean newer customers are worth more.",
      when: (ctx) => ctx.view.cohorts.ltv.length > 0,
      chart: (ctx) => {
        const L = ctx.view.cohorts.ltv, n = Math.max(...L.map((l) => l.values.length));
        return { ...base(), tooltip: { ...base().tooltip, trigger: "axis", valueFormatter: (v) => (v == null ? "–" : `${fmt(v)} ${ctx.cur}`) },
          xAxis: axis({ type: "category", data: Array.from({ length: n }, (_, i) => "M" + i), boundaryGap: false, name: "Months after acquisition", nameLocation: "middle", nameGap: 26, nameTextStyle: { color: T.muted } }),
          yAxis: axis({ type: "value", axisLabel: { color: T.muted, formatter: (v) => compact(v) } }),
          grid: { left: 8, right: 16, top: 40, bottom: 36, containLabel: true },
          series: L.map((l, i) => ({ name: l.cohort, type: "line", symbol: "circle", symbolSize: 6, lineStyle: { width: 2 }, color: SERIES[i], data: l.values })) };
      },
      table: (ctx) => {
        const L = ctx.view.cohorts.ltv, n = Math.max(...L.map((l) => l.values.length));
        return { cols: [{ key: "m", label: "Month" }, ...L.map((l, i) => ({ key: "c" + i, label: l.cohort, num: 1, fmt: (v) => fmt(v) }))],
          rows: Array.from({ length: n }, (_, k) => ({ m: "M" + k, ...Object.fromEntries(L.map((l, i) => ["c" + i, l.values[k]])) })) };
      },
    },
    "coh-second": {
      title: "Time to second purchase", sub: () => "Share of new customers with a 2nd purchase within N days",
      how: "Only customers acquired at least N days before the snapshot count for each point, so the curve is not dragged down by customers who simply have not had time yet. Use the steep part of the curve to time second-purchase campaigns.",
      when: (ctx) => ctx.view.cohorts.second_purchase.length > 0,
      chart: (ctx) => {
        const s = ctx.view.cohorts.second_purchase, i90 = s.findIndex((p) => p.days === 90);
        return { ...base(), legend: { show: false }, tooltip: { ...base().tooltip, trigger: "axis", formatter: (p) => `Within ${s[p[0].dataIndex].days} days: <b>${pct(p[0].value, 1)}</b><br><span style="color:${T.muted}">${fmt(s[p[0].dataIndex].eligible)} customers old enough</span>` },
          xAxis: axis({ type: "category", data: s.map((p) => p.days + " d"), boundaryGap: false }), yAxis: axis({ type: "value", axisLabel: { color: T.muted, formatter: (v) => pct(v) } }),
          series: [{ type: "line", data: s.map((p) => p.share), symbol: "circle", symbolSize: 8, color: T.blue, lineStyle: { width: 2 }, areaStyle: { color: "rgba(40,70,220,.08)" },
            markPoint: i90 < 0 ? undefined : { symbol: "circle", symbolSize: 10, itemStyle: { color: T.blue, borderColor: "#fff", borderWidth: 2 }, data: [{ coord: [i90, s[i90].share] }],
              label: { show: true, position: "top", color: T.navy, fontWeight: 600, formatter: `${pct(s[i90].share)} by day 90` } } }] };
      },
      table: (ctx) => ({ cols: [{ key: "days", label: "Within days", num: 1 }, { key: "share", label: "Share with 2nd purchase", num: 1, fmt: (v) => pct(v, 1) }, { key: "eligible", label: "Customers old enough", num: 1, fmt: (v) => fmt(v) }], rows: ctx.view.cohorts.second_purchase }),
    },
    "coh-monthly": {
      title: "Monthly development", height: 400,
      sub: (ctx) => (ctx.state.trend === "aov" ? "Average order value of purchases" : "New vs returning customers"),
      controls: [{ key: "trend", options: [["revenue", "Revenue"], ["customers", "Customers"], ["aov", "AOV"]] }],
      how: "New = the customer's acquisition month. A shrinking new-customer share with stable returning revenue means growth comes from the base; the reverse means the business relies on acquisition.",
      when: (ctx) => ctx.view.cohorts.monthly.length > 0,
      chart: (ctx) => {
        const M = ctx.view.cohorts.monthly, labels = M.map((m) => m.month + (m.partial ? " *" : "")), t = ctx.state.trend;
        let series, yf, tf;
        if (t === "aov") {
          series = [{ name: "AOV", type: "line", data: M.map((m) => m.aov), symbol: "circle", symbolSize: 6, color: T.blue, lineStyle: { width: 2 }, areaStyle: { color: "rgba(40,70,220,.08)" } }];
          yf = (v) => compact(v); tf = (v) => `${fmt(v)} ${ctx.cur}`;
        } else {
          const k = t === "customers" ? "customers" : "revenue";
          series = [["Returning customers", k + "_returning", T.blue], ["New customers", k + "_new", T.sky]].map(([n, key, c], i) => ({ name: n, type: "bar", stack: "t", barMaxWidth: 26, color: c,
            data: M.map((m) => m[key]), itemStyle: { borderColor: "#fff", borderWidth: 1, borderRadius: i === 1 ? [4, 4, 0, 0] : 0 } }));
          yf = (v) => compact(v); tf = (v) => (t === "customers" ? `${fmt(v)} customers` : money(v, ctx.cur));
        }
        return { ...base(), tooltip: { ...base().tooltip, trigger: "axis", axisPointer: t === "aov" ? { type: "line" } : shadow, valueFormatter: tf },
          xAxis: axis({ type: "category", data: labels, axisLabel: { color: T.muted, fontSize: 11.5, formatter: monthTick(labels) } }),
          yAxis: axis({ type: "value", axisLabel: { color: T.muted, formatter: yf } }), series };
      },
      table: (ctx) => ({ cols: [{ key: "month", label: "Month" }, { key: "customers_new", label: "New customers", num: 1, fmt: (v) => fmt(v) }, { key: "customers_returning", label: "Returning", num: 1, fmt: (v) => fmt(v) },
        { key: "revenue_new", label: "Revenue new", num: 1, fmt: (v) => fmt(v) }, { key: "revenue_returning", label: "Revenue returning", num: 1, fmt: (v) => fmt(v) }, { key: "orders", label: "Orders", num: 1, fmt: (v) => fmt(v) }, { key: "aov", label: "AOV", num: 1, fmt: (v) => fmt(v) }],
        rows: ctx.view.cohorts.monthly }),
    },

    /* ================= MARKETS ================= */
    "mk-cards": {
      title: "Markets at a glance", sub: () => "Arrows compare each market with the average of all markets in view",
      when: (ctx) => !!ctx.mk,
      html: (ctx) => {
        const a = ctx.mk.average;
        return `<div class="grid mk-grid">${ctx.mk.rows.slice(0, 6).map((m, i) =>
          stat(`${esc(m.market)} · customers`, fmt(m.customers), `${pct(m.customer_share)} of customers · ${pct(m.revenue_share)} of revenue`, "lp") +
          stat(`${esc(m.market)} · revenue / customer`, `${fmt(m.revenue_per_customer)} ${ctx.cur}`, `${m.revenue_per_customer >= a.revenue_per_customer ? "+" : ""}${pct(m.revenue_per_customer / a.revenue_per_customer - 1)} vs average`, i === 0 ? "hl" : "", trend(m.revenue_per_customer, a.revenue_per_customer)) +
          stat(`${esc(m.market)} · repeat rate`, pct(m.repeat_rate), "bought 2+ times", "lt", trend(m.repeat_rate, a.repeat_rate))).join("")}</div>
          ${ctx.mk.rows.length > 6 ? `<p class="small muted">Showing the 6 largest markets; all markets are in the table below.</p>` : ""}`;
      },
    },
    "mk-table": {
      title: "Market KPIs", sub: (ctx) => `Segments with fewer than ${ctx.mk.small_threshold} customers in a market are flagged as too small to read`,
      when: (ctx) => !!ctx.mk,
      html: (ctx) => {
        const small = {};
        ctx.mk.small_segments.forEach((s) => (small[s.market] = small[s.market] || []).push(`${s.segment} · ${s.customers}`));
        return RFM.table([
          { key: "market", label: "Market", html: 1, fmt: (v) => `<span class="swatch" style="background:${marketColor(ctx, v)}"></span><b>${esc(v)}</b>` },
          { key: "customers", label: "Customers", num: 1, fmt: (v) => fmt(v) },
          { key: "net_revenue", label: "Net revenue", num: 1, fmt: (v) => compact(v) },
          { key: "revenue_per_customer", label: "Revenue / customer", num: 1, fmt: (v) => fmt(v) },
          { key: "aov", label: "AOV", num: 1, fmt: (v) => fmt(v) },
          { key: "repeat_rate", label: "Repeat rate", num: 1, fmt: (v) => pct(v) },
          { key: "return_rate", label: "Return rate", num: 1, fmt: (v) => pct(v, 1) },
          ...(ctx.R.meta.has.consent ? [{ key: "consent_share", label: "Consent", num: 1, fmt: (v) => pct(v) }] : []),
          { key: "market", label: "Small segments", html: 1, fmt: (v) => (small[v] ? small[v].map((s) => RFM.pill("warn", s)).join(" ") : RFM.pill("ok", "none")) },
        ], ctx.mk.rows);
      },
    },
    "mk-segshare": {
      title: "Segment mix by market", height: 460, when: (ctx) => !!ctx.mk,
      sub: (ctx) => (ctx.state.mkMix === "count" ? "Each market's customers by segment (customers)" : "Each market's customers by segment (100% per bar)"),
      controls: [{ key: "mkMix", options: [["share", "100%"], ["count", "Customers"]] }],
      how: "Compare the same colour across bars: a taller block means the market over-indexes on that segment. Switch to Customers to see absolute sizes. Hover a block to highlight its segment.",
      chart: (ctx) => stackMix(ctx, ctx.mk.rows.map((r) => r.market), ctx.mk.segment_share, "market", ctx.state.mkMix),
      onChart: hoverSeries,
      table: (ctx) => {
        const ms = ctx.mk.rows.map((r) => r.market);
        return { cols: [{ key: "segment", label: "Segment" }, ...ms.map((m) => ({ key: m, label: m, num: 1, fmt: (v) => pct(v, 1) }))],
          rows: ctx.order.map((s) => ({ segment: s, ...Object.fromEntries(ctx.mk.segment_share.filter((x) => x.segment === s).map((x) => [x.market, x.share])) })) };
      },
    },
    "mk-retention": {
      title: "Retention by market", sub: (ctx) => `Share of new customers active N ${ctx.view.cohorts.grain}s after acquisition${ctx.mk.retention.length > 4 ? " · 4 largest markets" : ""}`, height: 440,
      how: "Customer-weighted across all cohorts that can be observed at that point. A market whose line sits lower loses new customers faster.",
      when: (ctx) => !!ctx.mk && ctx.mk.retention.some((r) => r.curve.some((v) => v != null)),
      chart: (ctx) => {
        const L = ctx.mk.retention.slice(0, 4), n = Math.max(...L.map((l) => l.curve.length)), p = ctx.view.cohorts.grain === "quarter" ? "Q" : "M";
        return { ...base(), tooltip: { ...base().tooltip, trigger: "axis", valueFormatter: (v) => pct(v, 1) },
          xAxis: axis({ type: "category", data: Array.from({ length: n }, (_, i) => p + (i + 1)), boundaryGap: false }),
          yAxis: axis({ type: "value", axisLabel: { color: T.muted, formatter: (v) => pct(v) } }),
          series: L.map((l) => ({ name: l.market, type: "line", symbol: "circle", symbolSize: 6, lineStyle: { width: 2 }, color: marketColor(ctx, l.market), data: l.curve })) };
      },
      table: (ctx) => {
        const L = ctx.mk.retention, n = Math.max(...L.map((l) => l.curve.length)), p = ctx.view.cohorts.grain === "quarter" ? "Q" : "M";
        return { cols: [{ key: "market", label: "Market" }, { key: "acquired", label: "New customers", num: 1, fmt: (v) => fmt(v) }, ...Array.from({ length: n }, (_, i) => ({ key: "p" + i, label: p + (i + 1), num: 1, fmt: (v) => pct(v, 1) }))],
          rows: L.map((l) => ({ market: l.market, acquired: l.acquired, ...Object.fromEntries(l.curve.map((v, i) => ["p" + i, v])) })) };
      },
    },
    "mk-none": {
      plain: true, when: (ctx) => !ctx.mk,
      html: (ctx) => `<div class="card empty"><h3>No market comparison</h3><p class="muted">${ctx.R.meta.has.market ? "The current filters leave one market only." : "The transactions have no market column, or only one market."}</p></div>`,
    },

    /* ================= CATEGORIES (L3) — one story in three chapters =================
       01 Where we stand (situation) · 02 What's changing (complication, time lens) · 03 What to do (resolution) */
    "cat-none": {
      plain: true, when: (ctx) => !ctx.R.categories,
      html: () => `<div class="card" style="justify-items:start">${RFM.pill("na", "Needs the items table")}<h2>Categories</h2>
        <p class="muted" style="max-width:60ch">Category insights, shopper types, cross-sell moves and audiences unlock when the client provides the items table with item_category (and item_sub_category).</p></div>`,
    },
    "cat-ch1": { plain: true, when: (ctx) => !!ctx.cv, html: (ctx) => chapter("01", "Where we stand", ctx.cv.headlines.situation) },
    "cat-stats": {
      plain: true, when: (ctx) => !!ctx.cv,
      html: (ctx) => {
        const s = ctx.cv.summary;
        return `<div class="grid g4">${[
          stat("Categories", fmt(ctx.cv.categories.length), `${fmt(ctx.R.categories.meta.subs)} sub-categories`, "lp"),
          stat("Revenue from cross-shoppers", pct(s.cross_revenue_share), "customers who buy in 2+ categories", "hl"),
          stat("Cross-shoppers", pct(s.cross_share), `of customers · ${pct(s.multi_share)} buy in 3+`),
          stat("Categories per customer", fmt(s.avg_categories, 1), `${pct(1 - s.cross_share)} buy in one category only`, "lt"),
        ].join("")}</div>`;
      },
    },
    "cat-kpis": {
      title: "Category scoreboard", sub: (ctx) => `Click a category for its sub-categories · growth = last ${ctx.R.categories.meta.half_months} months vs the ${ctx.R.categories.meta.half_months} before`, when: (ctx) => !!ctx.cv,
      how: "Penetration is the share of customers in view who bought the category. Lapsing is the share of its buyers who have not bought anything recently (R score 1–2). A category with high revenue, falling growth and many lapsing buyers is where the next chapter looks first.",
      html: (ctx) => {
        const cols = [["revenue", "Revenue", (v) => compact(v)], ["revenue_share", "Share", (v) => pct(v, 1)], ["growth", "Growth", growthCell],
          ["customers", "Buyers", (v) => fmt(v)], ["penetration", "Penetration", (v) => pct(v)], ["repeat_rate", "Repeat rate", (v) => pct(v)],
          ["lapsing", "Lapsing", (v) => pct(v)], ["return_rate", "Return rate", (v) => pct(v, 1)], ["aov", "AOV", (v) => fmt(v)], ["discount_rate", "Discount", (v) => pct(v, 1)]];
        const cells = (r) => cols.map(([k, , f]) => `<td class="n">${f === growthCell ? f(r[k]) : esc(f(r[k]))}</td>`).join("");
        const rows = ctx.cv.kpis.map((k) => {
          const open = !!ctx.state.expanded[k.name];
          return `<tr><td><button class="expander" data-expand="${esc(k.name)}" aria-expanded="${open}"><span class="swatch" style="background:${RFM.categoryColor(ctx.R, k.name)}"></span><b>${esc(k.name)}</b></button></td>${cells(k)}</tr>` +
            k.subs.map((s) => `<tr class="sub-row" data-parent="${esc(k.name)}" ${open ? "" : "hidden"}><td>${esc(s.name)} ${s.small ? RFM.pill("warn", "< " + ctx.R.categories.meta.small + " buyers") : ""}</td>${cells(s)}</tr>`).join("");
        }).join("");
        return `<div class="table-wrap"><table class="t"><thead><tr><th>Category</th>${cols.map(([, l]) => `<th class="n">${l}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div>`;
      },
    },
    "cat-breadth": {
      title: "Breadth pays", sub: (ctx) => `Value by number of categories bought · ${pct(1 - ctx.cv.summary.cross_share)} of customers buy in one only`, when: (ctx) => !!ctx.cv && ctx.cv.breadth.length > 1,
      controls: [{ key: "breadth", options: [["revenue_per_customer", "Revenue"], ["repeat_rate", "Repeat"], ["avg_orders", "Orders"]] }],
      how: "Customers who buy across more categories are worth more and come back more often. The step from one to two categories is the cheapest to influence — it is the first move in chapter 03.",
      chart: (ctx) => {
        const b = ctx.cv.breadth, k = ctx.state.breadth, f = k === "repeat_rate" ? (v) => pct(v) : k === "avg_orders" ? (v) => fmt(v, 1) : (v) => compact(v);
        return { ...base(), legend: { show: false }, grid: { left: 8, right: 16, top: 28, bottom: 30, containLabel: true },
          tooltip: { ...base().tooltip, trigger: "axis", axisPointer: shadow, formatter: (ps) => { const r = b[ps[0].dataIndex]; return `<b>${r.n} categor${r.n === 1 ? "y" : "ies"}</b><br>${fmt(r.customers)} customers (${pct(r.share)})<br>Revenue / customer: ${fmt(r.revenue_per_customer)} ${ctx.cur}<br>Repeat rate: ${pct(r.repeat_rate)} · Orders: ${fmt(r.avg_orders, 1)}`; } },
          xAxis: axis({ type: "category", data: b.map((r) => `${r.n}`), name: "Categories bought", nameLocation: "middle", nameGap: 26, nameTextStyle: { color: T.muted } }),
          yAxis: axis({ type: "value", axisLabel: { color: T.muted, formatter: f } }),
          series: [{ type: "bar", data: b.map((r) => r[k]), barMaxWidth: 48, itemStyle: { color: T.blue, borderRadius: [4, 4, 0, 0] },
            label: { show: true, position: "top", color: T.navy, fontWeight: 600, fontSize: 11.5, formatter: (p) => f(p.value) + (k === "revenue_per_customer" && p.dataIndex ? `  ${fmt(p.value / b[0][k], 1)}×` : "") } }] };
      },
      table: (ctx) => ({ cols: [{ key: "n", label: "Categories", num: 1 }, { key: "customers", label: "Customers", num: 1, fmt: (v) => fmt(v) }, { key: "share", label: "Share", num: 1, fmt: (v) => pct(v, 1) },
        { key: "revenue_per_customer", label: "Revenue / customer", num: 1, fmt: (v) => fmt(v) }, { key: "repeat_rate", label: "Repeat rate", num: 1, fmt: (v) => pct(v) }, { key: "avg_orders", label: "Orders", num: 1, fmt: (v) => fmt(v, 1) }], rows: ctx.cv.breadth }),
    },
    "cat-shoppers": {
      title: "Who the customers are", sub: (ctx) => `Shopper types from ${ctx.R.categories.meta.basis === "orders" ? "orders" : "item revenue"} · ${pct(ctx.R.categories.meta.dominance)}+ in one category = “[category] shopper”`, height: 360, when: (ctx) => !!ctx.cv,
      how: "Types come from the categories the client sends — nothing is hard-coded. Compare customer share with revenue share: multi-category shoppers are usually a small group that carries a large part of revenue. Each type is also an audience.",
      chart: (ctx) => {
        const rows = shopperOrder(ctx.cv.shopper_types).reverse();
        return { ...base(), grid: { left: 8, right: 44, top: 40, bottom: 8, containLabel: true },
          tooltip: { ...base().tooltip, trigger: "axis", axisPointer: shadow, formatter: (ps) => { const r = rows[ps[0].dataIndex]; return `<b>${esc(r.type)}</b><br>${fmt(r.customers)} customers · ${pct(r.customer_share, 1)}<br>${pct(r.revenue_share, 1)} of revenue · ${fmt(r.revenue_per_customer)} ${ctx.cur} per customer<br>Repeat rate ${pct(r.repeat_rate)}`; } },
          xAxis: axis({ type: "value", axisLabel: { color: T.muted, formatter: (v) => pct(v) } }),
          yAxis: axis({ type: "category", data: rows.map((r) => r.type), axisLine: { show: false }, axisLabel: { color: T.navy, fontSize: 12 } }),
          series: [
            { name: "Share of customers", type: "bar", data: rows.map((r) => r.customer_share), barWidth: 9, barGap: "25%", itemStyle: { color: T.sky, borderRadius: [0, 4, 4, 0] }, label: { show: true, position: "right", color: T.ink2, fontSize: 11, formatter: (p) => pct(p.value) } },
            { name: "Share of revenue", type: "bar", data: rows.map((r) => r.revenue_share), barWidth: 9, itemStyle: { color: T.blue, borderRadius: [0, 4, 4, 0] }, label: { show: true, position: "right", color: T.navy, fontWeight: 600, fontSize: 11, formatter: (p) => pct(p.value) } },
          ] };
      },
      table: (ctx) => ({ cols: [{ key: "type", label: "Shopper type" }, { key: "customers", label: "Customers", num: 1, fmt: (v) => fmt(v) }, { key: "customer_share", label: "Share of customers", num: 1, fmt: (v) => pct(v, 1) },
        { key: "revenue_share", label: "Share of revenue", num: 1, fmt: (v) => pct(v, 1) }, { key: "revenue_per_customer", label: "Revenue / customer", num: 1, fmt: (v) => fmt(v) }, { key: "repeat_rate", label: "Repeat rate", num: 1, fmt: (v) => pct(v) }],
        rows: shopperOrder(ctx.cv.shopper_types) }),
    },

    "cat-ch2": { plain: true, when: (ctx) => !!ctx.cv, html: (ctx) => chapter("02", "What's changing", ctx.cv.headlines.complication) },
    "cat-momentum": {
      title: "Momentum by category", sub: (ctx) => `Last ${ctx.R.categories.meta.half_months} months vs the ${ctx.R.categories.meta.half_months} before`, height: 340, when: (ctx) => !!ctx.cv && ctx.cv.kpis.some((k) => k.growth != null),
      controls: [{ key: "momentum", options: [["growth", "Revenue"], ["buyers_growth", "Buyers"], ["rpb_growth", "Revenue per buyer"]] }],
      how: "Revenue growth splits into buyers and revenue per buyer. Fewer buyers is a reach problem (reactivation, prospecting); lower spend per buyer is a value problem (assortment, bundles, price). Switch the view to see which one drives each category.",
      chart: (ctx) => {
        const k = ctx.state.momentum, rows = ctx.cv.kpis.filter((r) => r[k] != null).slice().sort((a, b) => a[k] - b[k]);
        return { ...base(), legend: { show: false }, grid: { left: 8, right: 56, top: 16, bottom: 8, containLabel: true },
          tooltip: { ...base().tooltip, trigger: "axis", axisPointer: shadow, formatter: (ps) => { const r = rows[ps[0].dataIndex]; return `<b>${esc(r.name)}</b><br>Revenue ${signed(r.growth)}<br>Buyers ${signed(r.buyers_growth)}<br>Revenue per buyer ${signed(r.rpb_growth)}`; } },
          xAxis: axis({ type: "value", axisLabel: { color: T.muted, formatter: (v) => signed(v) } }),
          yAxis: axis({ type: "category", data: rows.map((r) => r.name), axisLine: { show: false }, axisLabel: { color: T.navy, fontWeight: 600 } }),
          series: [{ type: "bar", barWidth: 16, data: rows.map((r) => ({ value: r[k], itemStyle: { color: r[k] >= 0 ? T.blue : NEG, borderRadius: r[k] >= 0 ? [0, 4, 4, 0] : [4, 0, 0, 4] } })),
            label: { show: true, position: "right", color: T.navy, fontWeight: 600, fontSize: 11.5, formatter: (p) => signed(p.value) } }] };
      },
      table: (ctx) => ({ cols: [{ key: "name", label: "Category" }, { key: "growth", label: "Revenue", num: 1, fmt: signed }, { key: "buyers_growth", label: "Buyers", num: 1, fmt: signed }, { key: "rpb_growth", label: "Revenue per buyer", num: 1, fmt: signed }], rows: ctx.cv.kpis }),
    },
    "cat-cycle": {
      title: "How fast buyers come back", sub: () => "Buyers who bought the same category again within 90 / 180 days · typical gap in days", height: 340, when: (ctx) => !!ctx.cv,
      how: "Only buyers whose first purchase in the category was at least 180 days ago count, so recent buyers do not drag the numbers down. A short typical gap calls for replenishment reminders; a long one for inspiration and cross-sell instead.",
      chart: (ctx) => {
        const rows = ctx.cv.cycle.slice().sort((a, b) => (a.back_180 || 0) - (b.back_180 || 0)), lab = rows.map((r) => `${r.category} · ${r.median_days == null ? "–" : fmt(r.median_days) + " d"}`);
        return { ...base(), grid: { left: 8, right: 44, top: 40, bottom: 8, containLabel: true },
          tooltip: { ...base().tooltip, trigger: "axis", axisPointer: shadow, formatter: (ps) => { const r = rows[ps[0].dataIndex]; return `<b>${esc(r.category)}</b><br>Back within 90 days: ${pct(r.back_90)}<br>Back within 180 days: ${pct(r.back_180)}<br>Typical gap: ${fmt(r.median_days)} days<br><span style="color:${T.muted}">${fmt(r.buyers)} buyers</span>`; } },
          xAxis: axis({ type: "value", axisLabel: { color: T.muted, formatter: (v) => pct(v) } }),
          yAxis: axis({ type: "category", data: lab, axisLine: { show: false }, axisLabel: { color: T.navy, fontSize: 12 } }),
          series: [
            { name: "Within 90 days", type: "bar", data: rows.map((r) => r.back_90), barWidth: 9, barGap: "25%", itemStyle: { color: T.sky, borderRadius: [0, 4, 4, 0] }, label: { show: true, position: "right", color: T.ink2, fontSize: 11, formatter: (p) => pct(p.value) } },
            { name: "Within 180 days", type: "bar", data: rows.map((r) => r.back_180), barWidth: 9, itemStyle: { color: T.blue, borderRadius: [0, 4, 4, 0] }, label: { show: true, position: "right", color: T.navy, fontWeight: 600, fontSize: 11, formatter: (p) => pct(p.value) } },
          ] };
      },
      table: (ctx) => ({ cols: [{ key: "category", label: "Category" }, { key: "buyers", label: "Buyers", num: 1, fmt: (v) => fmt(v) }, { key: "median_days", label: "Typical gap (days)", num: 1, fmt: (v) => fmt(v) },
        { key: "back_90", label: "Within 90 days", num: 1, fmt: (v) => pct(v, 1) }, { key: "back_180", label: "Within 180 days", num: 1, fmt: (v) => pct(v, 1) }], rows: ctx.cv.cycle }),
    },
    "cat-trend": {
      title: "Category development", sub: (ctx) => (ctx.state.catTrend === "share" ? "Share of monthly item revenue by category" : `Net item revenue by month, ${ctx.cur}`), height: 380, when: (ctx) => !!ctx.cv && ctx.cv.trend.length > 1,
      controls: [{ key: "catTrend", options: [["abs", "Revenue"], ["share", "Share"]] }],
      how: "Shows which categories drive growth and how the mix shifts with the season. A category losing share month after month while total revenue grows deserves a closer look.",
      chart: (ctx) => {
        const T_ = ctx.cv.trend, cats = ctx.cv.categories, share = ctx.state.catTrend === "share", labels = T_.map((r) => r.month);
        const tot = T_.map((r) => cats.reduce((a, c) => a + Math.max(r[c] || 0, 0), 0));
        return { ...base(), tooltip: { ...base().tooltip, trigger: "axis", axisPointer: shadow, valueFormatter: (v) => (share ? pct(v, 1) : money(v, ctx.cur)) },
          xAxis: axis({ type: "category", data: labels, axisLabel: { color: T.muted, fontSize: 11.5, formatter: (v) => (v.slice(5) === "01" || v === labels[0] ? v : v.slice(5)) } }),
          yAxis: axis({ type: "value", max: share ? 1 : null, axisLabel: { color: T.muted, formatter: (v) => (share ? pct(v) : compact(v)) } }),
          series: cats.map((c) => ({ name: c, type: "bar", stack: "t", barMaxWidth: 26, color: RFM.categoryColor(ctx.R, c), itemStyle: { borderColor: "#fff", borderWidth: 1 },
            data: T_.map((r, j) => (share ? (tot[j] ? Math.max(r[c] || 0, 0) / tot[j] : 0) : r[c] || 0)) })) };
      },
      table: (ctx) => ({ cols: [{ key: "month", label: "Month" }, ...ctx.cv.categories.map((c) => ({ key: c, label: c, num: 1, fmt: (v) => fmt(v) }))], rows: ctx.cv.trend }),
    },
    "cat-segmix": {
      title: "Where buyers are slipping away", height: 460, when: (ctx) => !!ctx.cv,
      sub: (ctx) => (ctx.state.mixMode === "count" ? "Each category's buyers by overall RFM segment (customers)" : "Each category's buyers by overall RFM segment (100% per bar)"),
      controls: [{ key: "mixMode", options: [["share", "100%"], ["count", "Customers"]] }],
      how: "A category whose bar is dominated by Hibernating, Needs Attention or At Risk customers is losing its buyers: reactivation pays back there before acquisition. Switch to Customers to see absolute sizes. Hover a block to highlight its segment.",
      chart: (ctx) => stackMix(ctx, ctx.cv.categories, ctx.cv.segment_mix, "category", ctx.state.mixMode),
      onChart: hoverSeries,
      table: (ctx) => ({ cols: [{ key: "segment", label: "Segment" }, ...ctx.cv.categories.map((c) => ({ key: c, label: c, num: 1, fmt: (v) => pct(v, 1) }))],
        rows: ctx.order.map((s) => ({ segment: s, ...Object.fromEntries(ctx.cv.segment_mix.filter((x) => x.segment === s).map((x) => [x.category, x.share])) })) }),
    },

    "cat-ch3": { plain: true, when: (ctx) => !!ctx.cv && ctx.cv.plays.length > 0, html: (ctx) => chapter("03", "What to do", ctx.cv.headlines.resolution) },
    "cat-plays": {
      plain: true, when: (ctx) => !!ctx.cv && ctx.cv.plays.length > 0,
      html: (ctx) => `<div class="plays n${ctx.cv.plays.length}">${ctx.cv.plays.map((p, i) => `<article class="play ${i === 0 ? "hl" : ""}">
          <div class="play-top"><span class="play-n">${String(i + 1).padStart(2, "0")}</span><span class="pill info">${p.kicker}</span></div>
          <div class="play-metric">${p.metric}</div><div class="play-ml">${p.metric_label}</div>
          <h3>${p.title}</h3>
          <dl><dt>Why</dt><dd>${p.why}</dd><dt>Who</dt><dd>${p.who}</dd><dt>How</dt><dd>${p.how}</dd><dt>Measure</dt><dd>${p.measure}</dd></dl>
        </article>`).join("")}</div>
        <p class="small muted">Moves are generated from the numbers and ranked by an indicative 5% response on each move's audience. Use them as a starting point for the plan, not as a forecast.</p>`,
    },
    "cat-next": {
      title: "Next best category", sub: (ctx) => (ctx.state.nextMode === "matrix" ? "Of the buyers of a row's category, the share who also bought the column's" : "What to recommend after each category"),
      when: (ctx) => !!ctx.cv && ctx.cv.next.length > 0,
      controls: [{ key: "nextMode", options: [["list", "Recommendations"], ["matrix", "All pairs"]] }],
      how: "The recommendation is the category with the highest lift: how much more likely than average a category's buyers are to also buy it (1.0× = average). “Not yet” is the audience: buyers of the category who have not bought the recommended one. Association only — test against a holdout.",
      useHtml: (ctx) => ctx.state.nextMode !== "matrix",
      height: 340,
      html: (ctx) => RFM.table([
        { key: "category", label: "Bought", html: 1, fmt: (v) => `<span class="swatch" style="background:${RFM.categoryColor(ctx.R, v)}"></span><b>${esc(v)}</b>` },
        { key: "next", label: "Recommend next", html: 1, fmt: (v) => `<span class="swatch" style="background:${RFM.categoryColor(ctx.R, v)}"></span>${esc(v)}` },
        { key: "lift", label: "Lift", num: 1, fmt: (v) => fmt(v, 2) + "×" },
        { key: "share", label: "Already buy it", num: 1, fmt: (v) => pct(v) },
        { key: "not_yet", label: "Not yet (audience)", num: 1, fmt: (v) => fmt(v) },
        { key: "common_second", label: "Most common 2nd order", fmt: (v) => v || "–" },
      ], ctx.cv.next),
      chart: (ctx) => {
        const cats = ctx.cv.categories, data = ctx.cv.cross.map((c) => [cats.indexOf(c.b), cats.indexOf(c.a), c.share, c.lift, c.both]);
        const mx = Math.max(...data.map((d) => d[2]), 0.01);
        return { ...base(), grid: { left: 8, right: 8, top: 36, bottom: 8, containLabel: true },
          tooltip: { ...base().tooltip, formatter: (p) => `Of <b>${esc(cats[p.value[1]])}</b> buyers, <b>${pct(p.value[2])}</b> also bought ${esc(cats[p.value[0]])}<br>Lift ${fmt(p.value[3], 2)}× · ${fmt(p.value[4])} customers` },
          xAxis: axis({ type: "category", data: cats, position: "top", axisLine: { show: false }, axisLabel: { color: T.navy, fontWeight: 600 } }),
          yAxis: axis({ type: "category", data: cats, inverse: true, axisLine: { show: false }, axisLabel: { color: T.navy, fontWeight: 600 } }),
          visualMap: { show: false, min: 0, max: mx, dimension: 2, inRange: { color: T.seq } },
          series: [{ type: "heatmap", data: data.map((d) => ({ value: d, label: RFM.cellLabel(d[2], mx) })), itemStyle: { borderColor: "#fff", borderWidth: 3, borderRadius: 6 }, label: { show: true, fontSize: 11.5, formatter: (p) => pct(p.value[2]) } }] };
      },
    },
    "cat-flow": {
      title: "What new customers buy next", sub: () => "Category of the first order (left) and the second order (right)", height: 400, when: (ctx) => !!ctx.cv && ctx.cv.sankey.length > 0,
      how: "Follow a band from left to right. Wide bands into another category are natural next-category recommendations for onboarding; the grey block is new customers who have not come back yet — the size of the onboarding challenge.",
      chart: (ctx) => {
        const links = ctx.cv.sankey, names = [...new Set(links.flatMap((l) => [l.source, l.target]))], plain = (n) => n.replace(" ·2nd", "");
        return { ...base(), animation: false, tooltip: { ...base().tooltip, formatter: (p) => (p.dataType === "edge" ? `${fmt(p.data.value)} customers started with <b>${esc(plain(p.data.source))}</b> and ${p.data.target.startsWith("No 2nd") ? "have not bought again" : `next bought <b>${esc(plain(p.data.target))}</b>`}` : `<b>${esc(plain(p.name))}</b>`) },
          series: [{ type: "sankey", left: 8, right: 150, top: 8, bottom: 8, nodeGap: 10, nodeWidth: 14, draggable: false, emphasis: { focus: "adjacency" },
            data: names.map((n) => ({ name: n, itemStyle: { color: n.startsWith("No 2nd") ? T.line : RFM.categoryColor(ctx.R, plain(n)), borderWidth: 0 } })),
            links, lineStyle: { color: "gradient", opacity: 0.35, curveness: 0.5 }, label: { color: T.navy, fontSize: 12, formatter: (p) => plain(p.name) } }] };
      },
      table: (ctx) => ({ cols: [{ key: "source", label: "First order" }, { key: "target", label: "Second order", fmt: (v) => v.replace(" ·2nd", "") }, { key: "value", label: "Customers", num: 1, fmt: (v) => fmt(v) }], rows: ctx.cv.sankey }),
    },
    "cat-aud": {
      title: "Cross-sell audiences", sub: (ctx) => `Valuable segments who never bought a category that goes with what they buy · revenue in play at ${pct(+ctx.state.conv)} conversion`,
      when: (ctx) => !!ctx.cv && ctx.cv.audiences.length > 0,
      controls: [{ key: "conv", options: [["0.02", "2%"], ["0.05", "5%"], ["0.1", "10%"]] }],
      how: "Affinity is the average lift between the categories each customer already buys and the target category (1.0× = average); only customers at 1.0× or more are included. Revenue in play = customers × conversion × the category's average order value — a scenario, not a forecast. Audiences overlap.",
      html: (ctx) => {
        const act = { "Champions": "VIP early access to the category", "Loyal High Value": "Bundle with their main category", "Potential Loyalists": "Second-category offer after purchase",
          "New Customers": "Feature the category in onboarding", "At Risk High Value": "Win-back with category inspiration" };
        const rows = ctx.cv.audiences.map((a) => ({ ...a, name: `${a.segment} → ${a.category}`, play: a.customers * +ctx.state.conv * (a.order_value || 0), action: act[a.segment] || "Cross-sell campaign" }));
        return RFM.table([
          { key: "name", label: "Audience", html: 1, fmt: (v, r) => `<span class="swatch" style="background:${RFM.categoryColor(ctx.R, r.category)}"></span><b>${esc(v)}</b>` },
          { key: "customers", label: "Customers", num: 1, fmt: (v) => fmt(v) },
          { key: "consented", label: "With consent", num: 1, fmt: (v) => fmt(v) },
          { key: "affinity", label: "Affinity", num: 1, fmt: (v) => fmt(v, 2) + "×" },
          { key: "play", label: "Revenue in play", num: 1, fmt: (v) => money(v, ctx.cur) },
          { key: "action", label: "Suggested action" },
          { key: "name", label: "", html: 1, fmt: () => `<button class="btn btn-ghost btn-sm" data-export title="Downloads the customer list (hashed emails) for Customer Match / Meta">Export</button>` },
        ], rows) + `<p class="small muted">Exports contain hashed emails and customer IDs — handle them like client data.</p>`;
      },
    },
    "cat-crm": {
      title: "Category CRM segments", sub: (ctx) => `Customers scored within each category (own cut-offs) · ${ctx.state.crmMode === "consent" ? "with marketing consent" : "all customers"}`,
      when: (ctx) => !!ctx.cv && ctx.cv.crm.length > 0,
      controls: [{ key: "crmMode", options: [["all", "Customers"], ["consent", "With consent"]] }],
      how: "Each customer is scored again on their purchases in each category, with cut-offs for that category's buyers, and placed in the same segments. Use the cells as ready-made audiences for category campaigns — e.g. Hibernating in Beauty for a Beauty reactivation. A “Champion” in a small category is not the same spend as one in a large category.",
      html: (ctx) => {
        const cats = ctx.cv.categories.filter((c) => ctx.cv.crm[0][c] != null), suf = ctx.state.crmMode === "consent" ? "|consent" : "";
        const mx = Object.fromEntries(cats.map((c) => [c, Math.max(...ctx.cv.crm.map((r) => r[c + suf] || 0), 1)]));
        const rows = ctx.cv.crm.filter((r) => cats.some((c) => r[c])).map((r) => `<tr><td><b>${esc(r.segment)}</b></td>${cats.map((c) => {
          const v = r[c + suf] || 0, a = v / mx[c];
          return `<td class="n heat" style="background:rgba(40,70,220,${(0.06 + a * 0.5).toFixed(2)});color:${a > 0.6 ? "#fff" : "inherit"}">${fmt(v)}</td>`;
        }).join("")}</tr>`).join("");
        return `<div class="table-wrap"><table class="t"><thead><tr><th>Segment</th>${cats.map((c) => `<th class="n"><span class="swatch" style="background:${RFM.categoryColor(ctx.R, c)}"></span>${esc(c)}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div>`;
      },
    },
    "cat-notes": {
      plain: true, when: (ctx) => !!ctx.cv,
      html: (ctx) => {
        const m = ctx.R.categories.meta, gap = m.item_vs_transaction_gap;
        return `<details class="card notes"><summary><b>Methodology and caveats</b><span class="small muted">How the category numbers are calculated</span></summary><ul>
          <li><b>Revenue</b> comes from item lines, net of returned lines, converted to ${esc(ctx.cur)}. Item lines total ${pct(Math.abs(gap), 1)} ${gap >= 0 ? "more" : "less"} than transaction amounts (usually VAT, shipping or discounts on a different basis). RFM scores and segments use transaction amounts.</li>
          <li><b>Growth</b> compares the last ${m.half_months} months of the window with the ${m.half_months} months before. <b>Lapsing</b> = buyers with an overall R score of 1–2.</li>
          <li><b>Shopper types</b> use each customer's ${m.basis === "orders" ? "orders" : "item revenue"}: ${pct(m.dominance)}+ in one category makes a “[category] shopper”, two categories a “Cross-shopper”, three or more a “Multi-category shopper”.</li>
          <li><b>Category CRM segments</b> use cut-offs calculated for each category's buyers, so segments are comparable within a category, not across categories.</li>
          <li><b>Moves and revenue in play</b> are generated from the numbers and use an indicative response rate. Affinity and lift show what goes together, not what a campaign will cause — measure every move against a holdout.</li>
          <li>Categories beyond the ${m.max_named} largest are grouped as “Other”. Sub-categories with fewer than ${m.small} buyers are flagged as too small to read.</li>
        </ul></details>`;
      },
    },
  };

  // Tabs follow the RFM levels. A row with one id is full width; two ids sit side by side.
  const LAYOUT = {
    overview: { label: "Overview", rows: [["ov-stats"], ["ov-insights", "ov-share"], ["ov-conc", "ov-data"]] },
    segments: { label: "Segments", rows: [["seg-kpis"], ["seg-rf", "seg-map"], ["seg-mix"], ["seg-actions"], ["seg-cutoffs"]] },
    cohorts: { label: "Cohorts", rows: [["coh-stats"], ["coh-heat"], ["coh-ltv", "coh-second"], ["coh-monthly"]] },
    markets: { label: "Markets", rows: [["mk-none"], ["mk-cards"], ["mk-table"], ["mk-segshare"], ["mk-retention"]] },
    categories: { label: "Categories", rows: [["cat-none"],
      ["cat-ch1"], ["cat-stats"], ["cat-kpis"], ["cat-breadth", "cat-shoppers"],
      ["cat-ch2"], ["cat-momentum", "cat-cycle"], ["cat-trend"], ["cat-segmix"],
      ["cat-ch3"], ["cat-plays"], ["cat-next"], ["cat-flow"], ["cat-aud"], ["cat-crm"], ["cat-notes"]] },
  };

  RFM.CARDS = CARDS;
  RFM.LAYOUT = LAYOUT;
})(window);
