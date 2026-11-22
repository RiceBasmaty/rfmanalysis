/* RFM Studio — shared helpers (formatting, chart theme, small UI pieces).
   Used by the app and by the self-contained report page. No dependencies except ECharts. */
(function (global) {
  "use strict";
  const RFM = global.RFM = global.RFM || {};

  /* ---------- tokens (Precis brand) ---------- */
  RFM.T = {
    blue: "#2846DC", sky: "#5AB9FF", green: "#00A000", purple: "#AA91E1",
    navy: "#143255", muted: "#879191", line: "#DCE3E2", paper: "#F6F5F1", lp: "#EBF3FC", l: "#C8E1FF", ink2: "#323737",
    seq: ["#EBF3FC", "#C8E1FF", "#7A9BEA", "#2846DC", "#143255"],
  };
  // Categorical order validated for colour-vision deficiency (adjacent ΔE ≥ 15). Never cycle: max 4 series.
  RFM.SERIES = [RFM.T.blue, RFM.T.sky, RFM.T.green, RFM.T.purple];
  // Segments (up to 8, fixed by rule order): brand hues plus three extras, order validated with the dataviz
  // palette checker (adjacent CVD ΔE ≥ 15, normal-vision ΔE ≥ 15). Light fills always get a direct label.
  RFM.SEGMENT_COLORS = ["#2846DC", "#5AB9FF", "#00A000", "#AA91E1", "#EB6834", "#14A3A3", "#E8A600", "#E87BA4"];
  RFM.SEGMENT_INK = ["#fff", "#143255", "#fff", "#143255", "#fff", "#fff", "#143255", "#143255"];
  RFM.segmentColor = (i) => (i < RFM.SEGMENT_COLORS.length ? RFM.SEGMENT_COLORS[i] : RFM.T.muted);
  RFM.segmentInk = (i) => (i < RFM.SEGMENT_INK.length ? RFM.SEGMENT_INK[i] : "#fff");
  // Categories reuse the validated 8-colour order, fixed by revenue rank in the whole dataset ("Other" is grey),
  // so a category keeps its colour under every filter.
  RFM.categoryIndex = (R, name) => (R.categories ? R.categories.meta.categories.indexOf(name) : -1);
  RFM.categoryColor = (R, name) => { const i = RFM.categoryIndex(R, name); return i < 0 ? RFM.T.muted : RFM.segmentColor(i); };
  RFM.categoryInk = (R, name) => { const i = RFM.categoryIndex(R, name); return i < 0 ? "#fff" : RFM.segmentInk(i); };

  /* ---------- formatting ---------- */
  const nf = (d) => new Intl.NumberFormat("en-US", { maximumFractionDigits: d, minimumFractionDigits: d });
  RFM.fmt = (v, d = 0) => (v == null || Number.isNaN(v) ? "–" : nf(d).format(v));
  RFM.pct = (v, d = 0) => (v == null || Number.isNaN(v) ? "–" : nf(d).format(v * 100) + "%");
  RFM.compact = (v) => {
    if (v == null || Number.isNaN(v)) return "–";
    const a = Math.abs(v);
    return a >= 1e9 ? nf(2).format(v / 1e9) + "B" : a >= 1e6 ? nf(1).format(v / 1e6) + "M" : a >= 1e4 ? nf(0).format(v / 1e3) + "k" : nf(0).format(v);
  };
  RFM.money = (v, cur) => (v == null ? "–" : RFM.compact(v) + " " + (cur || ""));
  RFM.esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  RFM.date = (iso) => (iso ? new Date(iso).toLocaleString("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "–");

  /* ---------- tiny DOM helper ---------- */
  RFM.h = (html) => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; };
  RFM.$ = (sel, root = document) => root.querySelector(sel);
  RFM.$$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  RFM.toast = (msg) => {
    const el = RFM.h(`<div class="toast" role="status">${RFM.esc(msg)}</div>`);
    document.body.appendChild(el); setTimeout(() => el.remove(), 2600);
  };

  /* ---------- UI pieces ---------- */
  // tone: "" (white) | "lp" (light+) | "lt" (light) | "hl" (dark highlight)
  RFM.stat = (label, value, sub, tone = "", trend = "") =>
    `<div class="stat ${tone}"><div class="k"><span>${label}</span>${trend}</div><div><div class="v">${value}</div><div class="s">${sub || ""}</div></div></div>`;
  RFM.trend = (v, avg) => (v == null || avg == null ? "" : v >= avg ? '<span class="tri up" aria-label="above average">▲</span>' : '<span class="tri down" aria-label="below average">▼</span>');
  RFM.pill = (kind, text) => `<span class="pill ${kind}">${RFM.esc(text)}</span>`;
  RFM.sevPill = (s) => RFM.pill({ critical: "crit", warning: "warn", info: "info", ok: "ok" }[s] || "na", s);
  RFM.banner = (kind, title, text, fix) => `<div class="banner ${kind}"><div class="ico" aria-hidden="true">${kind === "ok" ? "✓" : kind === "info" ? "i" : "!"}</div>
    <div><h4>${RFM.esc(title)}</h4>${text ? `<p>${RFM.esc(text)}</p>` : ""}${fix ? `<p class="fix"><b>How to fix:</b> ${RFM.esc(fix)}</p>` : ""}</div></div>`;
  RFM.table = (cols, rows, opts = {}) => {
    // cols: [{key, label, num, fmt(v,row), html}]
    const head = cols.map((c) => `<th class="${c.num ? "n" : ""}">${RFM.esc(c.label)}</th>`).join("");
    const body = rows.map((r) => `<tr class="${r._total ? "total" : ""}">` + cols.map((c) => {
      const v = r[c.key]; const s = c.fmt ? c.fmt(v, r) : v;
      return `<td class="${c.num ? "n" : ""}">${c.html ? (s ?? "") : RFM.esc(s ?? "–")}</td>`;
    }).join("") + "</tr>").join("");
    return `<div class="table-wrap ${opts.cls || ""}"><table class="t"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  };

  /* ---------- charts ---------- */
  const font = "Inter, 'Segoe UI', system-ui, sans-serif";
  RFM.base = () => ({
    textStyle: { fontFamily: font, color: RFM.T.ink2 },
    grid: { left: 8, right: 16, top: 40, bottom: 8, containLabel: true },
    tooltip: { confine: true, backgroundColor: "#fff", borderColor: RFM.T.line, borderWidth: 1, padding: [10, 12],
      textStyle: { color: RFM.T.navy, fontFamily: font, fontSize: 12.5 }, extraCssText: "border-radius:12px;box-shadow:0 6px 24px rgba(20,50,85,.12)" },
    legend: { top: 0, left: 0, icon: "roundRect", itemWidth: 12, itemHeight: 12, textStyle: { color: RFM.T.navy, fontSize: 12.5 } },
    animationDuration: 350,
  });
  RFM.axis = (extra = {}) => Object.assign({ axisLine: { lineStyle: { color: RFM.T.line } }, axisTick: { show: false },
    axisLabel: { color: RFM.T.muted, fontSize: 11.5 }, splitLine: { lineStyle: { color: RFM.T.paper } } }, extra);
  RFM.shadow = { type: "shadow", shadowStyle: { color: "rgba(40,70,220,.06)" } };
  RFM.cellLabel = (value, max) => ({ color: value > 0.6 * max ? "#fff" : RFM.T.navy });

  const instances = new Map();
  RFM.chart = (el, option) => {
    if (!el || !global.echarts) return null;
    let c = instances.get(el);
    if (!c) {
      c = global.echarts.init(el, null, { renderer: "svg" });
      instances.set(el, c);
      if (global.ResizeObserver) new ResizeObserver(() => c.resize()).observe(el);
    }
    c.setOption(option, true);
    return c;
  };
  RFM.disposeCharts = (root) => {
    for (const [el, c] of instances) if (!root || root.contains(el)) { c.dispose(); instances.delete(el); }
  };
})(window);
