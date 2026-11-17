/* RFM Studio — report renderer. Used inside the app (step 04) and by the self-contained shared page.
   RFM.Report.mount(rootElement, reportData, { actionsHtml, onTab }) */
(function (global) {
  "use strict";
  const RFM = global.RFM;
  const { esc, fmt, pct, h, $, $$ } = RFM;

  function mount(root, R, opts = {}) {
    const tabs = Object.keys(RFM.LAYOUT);
    const fromHash = (location.hash || "").slice(1);
    const state = { market: "all", channel: "all", loyalty: "all", tab: tabs.includes(fromHash) ? fromHash : "overview",
      cohMeasure: "customers", trend: "revenue", show: {} };
    const m = R.meta;
    const filters = [
      ["market", "All markets", R.filters.market], ["channel", "All channels", R.filters.channel], ["loyalty", "Members and non-members", R.filters.loyalty],
    ].filter(([, , o]) => o.length > 1);

    root.innerHTML = `
      <div class="report-root">
        <div class="page-head">
          <div>
            <div class="eyebrow">RFM analysis · ${esc(m.client)}</div>
            <h1>Customer segmentation report</h1>
            <p class="lead">Snapshot ${esc(m.snapshot_date)} · ${m.lookback_months}-month window (${esc(m.window_start)} → ${esc(m.window_end)}) · amounts in ${esc(m.currency)}, net of returns.</p>
          </div>
          <div class="actions">${opts.actionsHtml || ""}</div>
        </div>
        ${filters.length ? `<div class="filterbar" role="group" aria-label="Filters"><span class="fl">Filter</span>
          ${filters.map(([k, all, o]) => `<select data-filter="${k}" aria-label="${k}"><option value="all">${all}</option>${o.map((x) => `<option value="${esc(x.value)}">${esc(x.label)} · ${fmt(x.customers)}</option>`).join("")}</select>`).join("")}
          <span class="muted small" data-fsum></span><button class="btn btn-ghost reset" data-reset>Reset filters</button></div>` : ""}
        <div class="tabs" role="tablist">${tabs.map((t) => `<button class="tab" role="tab" data-tab="${t}" aria-selected="${t === state.tab}">${RFM.LAYOUT[t].label}${RFM.LAYOUT[t].later ? ' <span class="pill na" style="margin-left:6px">Later</span>' : ""}</button>`).join("")}</div>
        <div data-panel></div>
        <p class="foot">Generated ${esc(m.generated_at)} · RFM Studio engine ${esc(m.engine_version)}</p>
      </div>`;

    const panel = $("[data-panel]", root);

    function ctx() {
      const view = R.views[`${state.market}|${state.channel}|${state.loyalty}`];
      let mk = R.markets[`${state.channel}|${state.loyalty}`] || null;
      if (mk && state.market !== "all") mk = { ...mk, rows: mk.rows.filter((r) => r.market === state.market), retention: mk.retention.filter((r) => r.market === state.market) };
      if (mk && (!m.has.market || mk.rows.length < 1)) mk = null;
      return { R, view, mk, state, cur: m.currency, order: R.segments.order };
    }

    function card(id, c) {
      const def = RFM.CARDS[id];
      if (!def || (def.when && !def.when(c))) return null;
      if (def.plain) return h(`<div>${def.html(c)}</div>`);
      const showTable = state.show[id];
      const ctl = (def.controls || []).map((k) => `<div class="seg" role="group" aria-label="${k.key}">${k.options.map(([v, l]) => `<button data-ctl="${k.key}" data-v="${v}" aria-pressed="${c.state[k.key] === v}">${l}</button>`).join("")}</div>`).join("");
      const el = h(`<div class="card" data-card="${id}">
        <div class="card-head"><div><h3>${esc(def.title)}</h3>${def.sub ? `<div class="sub">${esc(def.sub(c))}</div>` : ""}</div>
          <div class="card-actions">${ctl}${def.how ? '<button class="btn btn-ghost btn-sm" data-how>How to read</button>' : ""}${def.table ? `<button class="btn btn-ghost btn-sm" data-table>${showTable ? "View as chart" : "View as table"}</button>` : ""}</div></div>
        ${def.how ? `<div class="how" hidden><b>How to read</b>${esc(def.how)}</div>` : ""}
        <div data-body></div></div>`);
      const body = $("[data-body]", el);
      if (showTable && def.table) {
        const t = def.table(c);
        body.innerHTML = RFM.table(t.cols, t.rows, { cls: "chart-table" });
      } else if (def.chart) {
        const box = h(`<div class="chart" style="height:${def.height || 340}px"></div>`);
        body.appendChild(box);
        el._draw = () => RFM.chart(box, def.chart(c));
      } else {
        body.innerHTML = def.html(c);
      }
      return el;
    }

    function render() {
      RFM.disposeCharts(panel);
      $$("[data-tab]", root).forEach((b) => b.setAttribute("aria-selected", b.dataset.tab === state.tab));
      const c = ctx();
      panel.innerHTML = "";
      if (!c.view) {
        panel.appendChild(h(`<div class="card empty"><h3>No customers match these filters</h3><p class="muted">Change or reset the filters.</p></div>`));
        return;
      }
      const wrap = h(`<div class="grid"></div>`);
      const draws = [];
      for (const row of RFM.LAYOUT[state.tab].rows) {
        const cards = row.map((id) => card(id, c)).filter(Boolean);
        if (!cards.length) continue;
        const r = cards.length > 1 ? h(`<div class="grid g2"></div>`) : null;
        cards.forEach((el) => { (r || wrap).appendChild(el); if (el._draw) draws.push(el._draw); });
        if (r) wrap.appendChild(r);
      }
      panel.appendChild(wrap);
      draws.forEach((d) => d());
      const sum = $("[data-fsum]", root);
      if (sum) sum.textContent = c.view && (state.market !== "all" || state.channel !== "all" || state.loyalty !== "all") ? `${fmt(c.view.customers)} customers in view` : "";
    }

    root.addEventListener("change", (e) => {
      const f = e.target.closest("[data-filter]");
      if (f) { state[f.dataset.filter] = f.value; render(); }
    });
    root.addEventListener("click", (e) => {
      const t = e.target.closest("[data-tab]");
      if (t) { state.tab = t.dataset.tab; if (opts.hashTabs !== false) history.replaceState(null, "", "#" + state.tab); render(); opts.onTab && opts.onTab(state.tab); return; }
      if (e.target.closest("[data-reset]")) { state.market = state.channel = state.loyalty = "all"; $$("[data-filter]", root).forEach((s) => (s.value = "all")); render(); return; }
      const cardEl = e.target.closest("[data-card]");
      if (!cardEl) return;
      const id = cardEl.dataset.card;
      if (e.target.closest("[data-how]")) { const box = $(".how", cardEl), b = e.target.closest("[data-how]"); box.hidden = !box.hidden; b.textContent = box.hidden ? "How to read" : "Hide explanation"; return; }
      if (e.target.closest("[data-table]")) { state.show[id] = !state.show[id]; render(); return; }
      const ctl = e.target.closest("[data-ctl]");
      if (ctl) { state[ctl.dataset.ctl] = ctl.dataset.v; render(); }
    });
    render();
    return { state, render };
  }

  RFM.Report = { mount };
})(window);
