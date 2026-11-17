/* RFM Studio — the internal app: runs list and the four steps (Connect, Settings, Data check, Report). */
(function () {
  "use strict";
  const { esc, fmt, pct, h, $, $$, stat, banner, T, SERIES, base, axis, shadow, compact } = RFM;
  const view = $("#view");
  const STEPS = [["connect", "Connect"], ["settings", "Settings"], ["check", "Data check"], ["report", "Report"]];
  const ROLES = ["transactions", "customers", "items", "fx_rates"];
  const ROLE_HINTS = { transactions: ["transaction", "order", "sales"], customers: ["customer", "member", "crm"], items: ["item", "line", "product"], fx_rates: ["fx", "rate", "currency", "exchange"] };
  let SPEC = null, RUN = null, poll = null;

  /* ---------------- API ---------------- */
  async function api(method, url, body) {
    const r = await fetch(url, { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
    const data = r.headers.get("content-type")?.includes("json") ? await r.json() : null;
    if (!r.ok) throw Object.assign(new Error("api"), { status: r.status, error: (data && data.error) || { title: `Request failed (${r.status})`, what: data?.detail || "", fix: "" } });
    return data;
  }
  const errBox = (e) => { const x = e.error || { title: "Something went wrong", what: String(e.message || e) }; return banner("crit", x.title, x.what, x.fix); };
  function showError(el, e) { el.innerHTML = errBox(e); el.hidden = false; el.scrollIntoView({ block: "nearest", behavior: "smooth" }); }

  /* ---------------- routing ---------------- */
  function go(path) { history.pushState(null, "", path); route(); }
  window.addEventListener("popstate", route);
  document.addEventListener("click", (e) => {
    const a = e.target.closest("a[data-link]");
    if (a && !e.metaKey && !e.ctrlKey) { e.preventDefault(); go(a.getAttribute("href")); }
  });

  async function route() {
    clearTimeout(poll);
    RFM.disposeCharts();
    if (!SPEC) SPEC = await api("GET", "/api/spec");
    const m = location.pathname.match(/^\/runs\/([0-9]{8}-[a-f0-9]{8})(?:\/(connect|settings|check|report))?\/?$/);
    if (!m) { RUN = null; topbar(); return home(); }
    try { RUN = await api("GET", `/api/runs/${m[1]}`); } catch (e) { RUN = null; topbar(); view.innerHTML = errBox(e) + `<p><a href="/" data-link>Back to all analyses</a></p>`; return; }
    const step = m[2] || (RUN.meta.report ? "report" : RUN.meta.check ? "check" : RUN.meta.source ? "settings" : "connect");
    if (!m[2]) history.replaceState(null, "", `/runs/${RUN.meta.id}/${step}`);
    topbar(step);
    ({ connect, settings, check, report })[step]();
  }

  function topbar(step) {
    const chip = $("#runChip"), st = $("#stepper");
    if (!RUN) { chip.innerHTML = ""; st.innerHTML = ""; return; }
    const snap = RUN.meta.report?.snapshot || RUN.meta.check?.snapshot;
    chip.innerHTML = `<span>Run</span><b>${esc(RUN.meta.client)}</b>${snap ? `<span>·</span><span>snapshot ${esc(snap)}</span>` : ""}`;
    const idx = STEPS.findIndex(([k]) => k === step), hasSource = !!RUN.meta.source;
    st.innerHTML = STEPS.map(([k, l], i) => {
      const disabled = !hasSource && i > 0;
      return `<a class="step ${i === idx ? "active" : i < idx ? "done" : ""}" ${disabled ? 'aria-disabled="true" tabindex="-1" style="pointer-events:none;opacity:.55"' : `href="/runs/${RUN.meta.id}/${k}" data-link`}><span class="n">0${i + 1}</span>${l}</a>`;
    }).join("");
  }

  const head = (eyebrow, title, lead, actions = "") => `<div class="page-head"><div><div class="eyebrow">${eyebrow}</div><h1>${title}</h1>${lead ? `<p class="lead">${lead}</p>` : ""}</div><div class="actions">${actions}</div></div>`;

  /* ---------------- home ---------------- */
  async function home() {
    view.innerHTML = head("RFM Studio", "RFM analyses", "Each analysis is one client and one snapshot. Start a new one, or duplicate a previous run to repeat it for a new period with the same settings.") +
      `<div class="card"><h3>Start a new analysis</h3><form class="new-run" id="newRun"><label class="sr" for="newClient" hidden>Client name</label>
        <input class="input" id="newClient" placeholder="Client name" maxlength="120" required><button class="btn btn-primary">Start →</button></form><div id="newErr" hidden></div></div>
       <div class="runs" id="runs"><div class="card">Loading…</div></div>`;
    $("#newRun").addEventListener("submit", async (e) => {
      e.preventDefault();
      try { const m = await api("POST", "/api/runs", { client: $("#newClient").value }); go(`/runs/${m.id}/connect`); } catch (er) { showError($("#newErr"), er); }
    });
    let runs = [];
    try { runs = await api("GET", "/api/runs"); } catch (e) { $("#runs").innerHTML = errBox(e); return; }
    if (!runs.length) { $("#runs").innerHTML = `<div class="card empty"><h3>No analyses yet</h3><p class="muted">Start one above.</p></div>`; return; }
    $("#runs").innerHTML = runs.map((r) => {
      const rep = r.report, chk = r.check;
      const state = rep ? RFM.pill("ok", "Report ready") : chk ? RFM.pill("info", "Data checked") : r.source ? RFM.pill("na", "Settings") : RFM.pill("na", "Draft");
      const line = rep ? `${fmt(rep.customers)} customers · ${RFM.money(rep.net_revenue, rep.currency)} · snapshot ${esc(rep.snapshot)}` : chk ? `${fmt(chk.customers)} customers · snapshot ${esc(chk.snapshot)}` : "Not connected yet";
      return `<div class="run-row"><div><a class="client" href="/runs/${r.id}" data-link>${esc(r.client)}</a><div class="meta">${line}</div></div>
        <div>${state}<div class="meta">Updated ${RFM.date(r.updated_at)}</div></div><div class="meta">${esc(r.id)}${r.cloned_from ? `<br>from ${esc(r.cloned_from)}` : ""}</div>
        <div class="row"><a class="btn btn-secondary btn-sm" href="/runs/${r.id}" data-link>Open</a><button class="btn btn-ghost btn-sm" data-dup="${r.id}">Duplicate</button><button class="btn btn-ghost btn-sm" data-del="${r.id}">Delete</button></div></div>`;
    }).join("");
    $("#runs").addEventListener("click", async (e) => {
      const dup = e.target.closest("[data-dup]"), del = e.target.closest("[data-del]");
      try {
        if (dup) { const m = await api("POST", "/api/runs", { clone_from: dup.dataset.dup }); go(`/runs/${m.id}/settings`); }
        if (del) {
          if (del.dataset.confirm) { await api("DELETE", `/api/runs/${del.dataset.del}`); RFM.toast("Analysis deleted"); home(); }
          else { del.dataset.confirm = "1"; del.textContent = "Click again to delete"; del.classList.add("btn-danger"); }
        }
      } catch (er) { RFM.toast(er.error?.title || "Failed"); }
    });
  }

  /* ---------------- 01 connect ---------------- */
  async function connect() {
    const src = RUN.meta.source || { type: "bigquery", project: SPEC.default_project, dataset: "", tables: {} };
    const hasLocal = SPEC.local_datasets.length > 0;
    view.innerHTML = head("Step 01 · Connect", "Where is the client's data?", "Point each table to BigQuery and check the column mapping. Only <b>transactions</b> is required; the other tables unlock more of the report.",
      `<button class="btn btn-primary" id="saveConnect" disabled>Save and continue →</button>`) +
      `<div id="err" hidden></div>
      <div class="grid g3">
        <div class="card field"><label for="client">Client name</label><input class="input" id="client" maxlength="120" value="${esc(RUN.config.client_name)}"><span class="hint">Shown on the report and the share link.</span></div>
        <button class="card source" data-src="bigquery" aria-pressed="${src.type === "bigquery"}"><div class="src-ico">BQ</div><h3>BigQuery</h3><p class="muted">Read the tables from a dataset. The app only reads the columns it needs.</p></button>
        ${hasLocal ? `<button class="card source" data-src="local" aria-pressed="${src.type === "local"}"><div class="src-ico">CSV</div><h3>Sample data</h3><p class="muted">Folders of CSVs on the server, for demos and testing.</p></button>` : `<div class="card"><h3>CSV upload</h3><p class="muted">Coming later. For now, load the client's files into BigQuery first.</p></div>`}
      </div>
      <div class="card" id="srcForm"></div>
      <div class="card" id="tablesCard" hidden></div>
      <div class="card" id="mapCard" hidden></div>`;
    let type = src.type, tables = [], info = null, mapping = {};

    function srcForm() {
      $("#srcForm").innerHTML = type === "bigquery"
        ? `<div class="card-head"><div><h3>BigQuery connection</h3><div class="sub">The app's service account needs BigQuery Data Viewer on the dataset and BigQuery Read Session User on the billing project.</div></div></div>
           <div class="form-grid"><div class="field"><label for="bqp">Billing project</label><input class="input" id="bqp" value="${esc(src.type === "bigquery" ? src.project || SPEC.default_project : SPEC.default_project)}" placeholder="my-gcp-project"></div>
           <div class="field"><label for="bqd">Dataset</label><input class="input" id="bqd" value="${esc(src.type === "bigquery" ? src.dataset : "")}" placeholder="project.dataset"></div>
           <div class="field"><label>&nbsp;</label><button class="btn btn-secondary" id="loadTables">Load tables</button></div></div>`
        : `<div class="card-head"><div><h3>Sample data</h3><div class="sub">CSV folders configured on the server.</div></div></div>
           <div class="form-grid"><div class="field"><label for="bqd">Folder</label><select class="input" id="bqd">${SPEC.local_datasets.map((d) => `<option ${src.dataset === d ? "selected" : ""}>${esc(d)}</option>`).join("")}</select></div>
           <div class="field"><label>&nbsp;</label><button class="btn btn-secondary" id="loadTables">Load files</button></div></div>`;
      $("#loadTables").addEventListener("click", loadTables);
    }
    const current = () => ({ type, project: type === "bigquery" ? $("#bqp").value.trim() : "", dataset: $("#bqd").value.trim(),
      tables: Object.fromEntries(ROLES.map((r) => [r, $(`#tbl-${r}`)?.value || ""]).filter(([, v]) => v)) });

    async function loadTables() {
      const btn = $("#loadTables"); btn.disabled = true; btn.textContent = "Loading…"; $("#err").hidden = true;
      try { tables = await api("POST", "/api/source/tables", current()); tablesCard(); }
      catch (e) { showError($("#err"), e); }
      finally { btn.disabled = false; btn.textContent = type === "bigquery" ? "Load tables" : "Load files"; }
    }
    function guess(role) {
      const assigned = (src.tables || {})[role];
      if (assigned && tables.some((t) => t.name === assigned)) return assigned;
      const t = tables.find((t) => ROLE_HINTS[role].some((k) => t.name.toLowerCase().includes(k)));
      return t ? t.name : "";
    }
    function tablesCard() {
      const c = $("#tablesCard"); c.hidden = false;
      c.innerHTML = `<div class="card-head"><div><h3>Tables</h3><div class="sub">${tables.length} found. Matched by name — change any that are wrong.</div></div></div><div>` +
        ROLES.map((r) => { const ti = SPEC.tables[r]; return `<div class="tbl-pick"><div><div class="tname">${esc(ti.label)}</div><span class="small muted">${esc(ti.requirement)} · ${esc(ti.description)}</span></div>
          <select class="input" id="tbl-${r}"><option value="">Not provided</option>${tables.map((t) => `<option value="${esc(t.name)}">${esc(t.name)}</option>`).join("")}</select><span data-info="${r}"></span></div>`; }).join("") +
        `</div><div class="row"><button class="btn btn-secondary" id="inspect">Check columns</button></div>`;
      ROLES.forEach((r) => { $(`#tbl-${r}`).value = guess(r); });
      const upd = () => ROLES.forEach((r) => { const t = tables.find((x) => x.name === $(`#tbl-${r}`).value); $(`[data-info="${r}"]`).innerHTML = t ? RFM.pill("ok", `${fmt(t.rows)} rows · ${t.columns.length} cols`) : ""; });
      c.addEventListener("change", upd); upd();
      $("#inspect").addEventListener("click", inspect);
    }
    async function inspect() {
      $("#err").hidden = true;
      const s = current();
      if (!s.tables.transactions) return showError($("#err"), { error: { title: "Transactions table not set", what: "Pick the transactions table.", fix: "" } });
      const btn = $("#inspect"); btn.disabled = true; btn.textContent = "Reading columns…";
      try {
        const res = await api("PUT", `/api/runs/${RUN.meta.id}/source`, s);
        RUN.meta.source = res.source; info = res.tables; topbar("connect");
        mapping = {};
        for (const role of Object.keys(info)) {
          const saved = RUN.config.column_map?.[role] || {}; const inv = Object.fromEntries(Object.entries(saved).map(([c, sp]) => [sp, c]));
          const lowerCols = Object.fromEntries(info[role].columns.map((c) => [c.toLowerCase(), c]));
          mapping[role] = {};
          for (const f of SPEC.spec[role]) {
            const fromSaved = inv[f.column] && lowerCols[inv[f.column]];
            mapping[role][f.column] = fromSaved || res.suggestions[role][f.column] || "";
          }
        }
        mapCard(Object.keys(info)[0]);
        $("#saveConnect").disabled = false;
      } catch (e) { showError($("#err"), e); }
      finally { btn.disabled = false; btn.textContent = "Check columns"; }
    }
    function mapCard(role) {
      const c = $("#mapCard"); c.hidden = false;
      const cols = info[role].columns, sample = info[role].sample;
      const sampleOf = (col) => { const i = cols.indexOf(col); return i < 0 ? "" : sample.map((r) => r[i]).filter(Boolean).slice(0, 2).join(" · "); };
      const matched = SPEC.spec[role].filter((f) => mapping[role][f.column]).length;
      c.innerHTML = `<div class="card-head"><div><h3>Column mapping</h3><div class="sub">${matched} of ${SPEC.spec[role].length} spec columns matched. Map the rest, or leave optional ones as “Not provided”.</div></div>
        <div class="seg" role="group" aria-label="Table">${Object.keys(info).map((r) => `<button data-role="${r}" aria-pressed="${r === role}">${esc(SPEC.tables[r].label)}</button>`).join("")}</div></div>
        <div class="table-wrap"><table class="t map-table"><thead><tr><th>Spec column</th><th>Requirement</th><th>Client column</th><th>Sample values</th></tr></thead><tbody>
        ${SPEC.spec[role].map((f) => `<tr><td><code>${esc(f.column)}</code><div class="small muted">${esc(f.description)}</div></td><td>${esc(f.requirement)}</td>
          <td><select class="input" data-map="${f.column}" aria-label="${esc(f.column)}"><option value="">Not provided</option>${cols.map((cc) => `<option ${mapping[role][f.column] === cc ? "selected" : ""}>${esc(cc)}</option>`).join("")}</select></td>
          <td class="sample" data-sample="${f.column}">${esc(sampleOf(mapping[role][f.column]))}</td></tr>`).join("")}</tbody></table></div>`;
      c.onclick = (e) => { const b = e.target.closest("[data-role]"); if (b) mapCard(b.dataset.role); };
      c.onchange = (e) => { const s = e.target.closest("[data-map]"); if (s) { mapping[role][s.dataset.map] = s.value; $(`[data-sample="${s.dataset.map}"]`, c).textContent = sampleOf(s.value); } };
    }
    async function save() {
      $("#err").hidden = true;
      const tx = mapping.transactions || {};
      const missing = ["transaction_id", "transaction_date", "transaction_amount"].filter((k) => !tx[k]);
      if (!tx.hashed_email && !tx.customer_id) missing.push("hashed_email or customer_id");
      if (missing.length) return showError($("#err"), { error: { title: "Required columns not mapped", what: missing.join(", "), fix: "Map them in the transactions column mapping." } });
      const column_map = {};
      for (const [role, m] of Object.entries(mapping)) {
        column_map[role] = {};
        for (const [spec, col] of Object.entries(m)) if (col && col.toLowerCase() !== spec) column_map[role][col.toLowerCase()] = spec;
      }
      try {
        const res = await api("PUT", `/api/runs/${RUN.meta.id}/config`, { ...RUN.config, client_name: $("#client").value, column_map });
        RUN.config = res.config; go(`/runs/${RUN.meta.id}/settings`);
      } catch (e) { showError($("#err"), e); }
    }
    $$("[data-src]").forEach((b) => b.addEventListener("click", () => {
      type = b.dataset.src; $$("[data-src]").forEach((x) => x.setAttribute("aria-pressed", x === b));
      tables = []; $("#tablesCard").hidden = $("#mapCard").hidden = true; $("#saveConnect").disabled = true; srcForm();
    }));
    $("#saveConnect").addEventListener("click", save);
    srcForm();
    if (RUN.meta.source && RUN.meta.source.type === type) { await loadTables(); if (tables.length) await inspect(); }
  }

  /* ---------------- 02 settings ---------------- */
  async function settings() {
    const c = JSON.parse(JSON.stringify(RUN.config));
    let baselines = [];
    try { baselines = (await api("GET", "/api/baselines")).filter((b) => b.id !== RUN.meta.id); } catch (e) { /* optional */ }
    const seg = (key, opts) => `<div class="seg" role="group" data-key="${key}">${opts.map(([v, l]) => `<button type="button" data-v="${v}" aria-pressed="${String(c[key]) === String(v)}">${l}</button>`).join("")}</div>`;
    view.innerHTML = head("Step 02 · Settings", "How should we score and slice it?", "The defaults suit most clients. Keep them unchanged between recurring runs of the same client so results stay comparable.",
      `<button class="btn btn-secondary" id="save">Save</button><button class="btn btn-primary" id="saveRun">Save and run data check →</button>`) +
      `<div id="err" hidden></div><div id="warn"></div>
      <div class="grid g2">
        <div class="card"><div class="card-head"><div><h3>Analysis window</h3><div class="sub">Recency is counted up to the snapshot date; the day itself is not included.</div></div></div>
          <div class="form-grid"><div class="field"><label for="snap">Snapshot date</label><input class="input" id="snap" type="date" value="${esc(c.snapshot_date || "")}" ${c.snapshot_date ? "" : "disabled"}></div>
          <div class="field"><label for="lookback">Lookback window (months)</label><input class="input" id="lookback" type="number" min="3" max="120" step="1" value="${c.lookback_months}"><span class="hint">12 for fast-moving categories, 24 default, 36 for long purchase cycles.</span></div></div>
          <label class="switch"><input type="checkbox" id="dayAfter" ${c.snapshot_date ? "" : "checked"}> Use the day after the latest transaction</label>
          <div class="field"><label for="cur">Reporting currency</label><input class="input" id="cur" list="curList" maxlength="3" value="${esc(c.reporting_currency)}"><datalist id="curList"><option>SEK</option><option>NOK</option><option>DKK</option><option>EUR</option><option>USD</option><option>GBP</option></datalist><span class="hint">All amounts are converted with the monthly FX table.</span></div>
        </div>
        <div class="card"><div class="card-head"><div><h3>Scoring</h3><div class="sub">R and M use quintiles; F uses fixed bins because most customers buy once or twice.</div></div></div>
          <div class="field"><label for="fbins">Frequency bins (lowest purchase count for F1–F5)</label><input class="input" id="fbins" value="${esc(c.frequency_bins.join(", "))}"><span class="hint" id="fbinsHint"></span></div>
          <div class="field"><label for="base">Cut-offs</label><select class="input" id="base"><option value="">Calculate fresh from this data</option>${baselines.map((b) => `<option value="${b.id}" ${c.baseline_run_id === b.id ? "selected" : ""}>Freeze from ${esc(b.client)} · ${esc(b.snapshot)}</option>`).join("")}</select><span class="hint">Freeze them to track how customers move between segments over time.</span></div>
        </div>
        <div class="card"><div class="card-head"><div><h3>Outliers and B2B</h3><div class="sub">Customers far above the bulk — usually B2B, staff or test accounts.</div></div></div>
          <div class="field"><label for="oq">Flag above quantile · <span id="oqv" class="num">${c.outlier_quantile}</span></label><input type="range" id="oq" min="0.9" max="0.999" step="0.001" value="${c.outlier_quantile}"></div>
          <div class="field"><label>Handling</label>${seg("outlier_action", [["exclude", "Exclude, report separately"], ["keep", "Keep in scoring"]])}</div>
          <div class="field"><label for="tol">Items check tolerance · <span id="tolv" class="num">${c.reconciliation_tolerance_pct}</span> %</label><input type="range" id="tol" min="0" max="10" step="0.5" value="${c.reconciliation_tolerance_pct}"></div>
        </div>
        <div class="card"><div class="card-head"><div><h3>Cohorts and markets</h3><div class="sub">Cohorts group customers by when they were acquired and follow them over time.</div></div></div>
          <div class="form-grid"><div class="field"><label>Cohort size</label>${seg("cohort_grain", [["month", "Month"], ["quarter", "Quarter"]])}</div>
          <div class="field"><label>Periods to follow</label>${seg("cohort_periods", [[6, "6"], [12, "12"], [24, "24"]])}</div></div>
          <div class="field"><label>Cohort start</label>${seg("cohort_start", [["first_ever", "First-ever purchase"], ["first_in_window", "First purchase in window"]])}<span class="hint">First-ever uses <code>first_purchase_date</code> from the customers table, so returning customers don't look new.</span></div>
          <div class="field"><label for="small">Flag market segments smaller than (customers)</label><input class="input" id="small" type="number" min="0" step="1" value="${c.small_segment_threshold}"></div>
        </div>
        <div class="card span-2"><div class="card-head"><div><h3>Segment rules</h3><div class="sub">Checked top to bottom — the first match wins. Use ${SPEC.rule_variables.map((v) => `<code>${v}</code>`).join(" ")} with <code>and</code> <code>or</code> <code>not</code>.</div></div>
          <div class="row"><button class="btn btn-ghost" id="resetRules" type="button">Reset to defaults</button><button class="btn btn-ghost" id="addRule" type="button">+ Add rule</button></div></div>
          <div class="stack" id="rules"></div></div>
      </div>`;
    const fbinsHint = () => { const b = $("#fbins").value.split(/[,\s;]+/).filter(Boolean).map(Number); $("#fbinsHint").textContent = b.length === 5 && b.every(Number.isInteger) ? "Means " + b.map((v, i) => (i < 4 ? (b[i + 1] - v === 1 ? v : `${v}–${b[i + 1] - 1}`) : `${v}+`)).join(" · ") : "Five ascending whole numbers starting at 1"; };
    fbinsHint(); $("#fbins").addEventListener("input", fbinsHint);
    $("#dayAfter").addEventListener("change", (e) => { $("#snap").disabled = e.target.checked; if (!e.target.checked && !$("#snap").value) $("#snap").value = new Date().toISOString().slice(0, 8) + "01"; });
    $("#oq").addEventListener("input", (e) => ($("#oqv").textContent = (+e.target.value).toFixed(3)));
    $("#tol").addEventListener("input", (e) => ($("#tolv").textContent = (+e.target.value).toFixed(1)));
    $$("[data-key]").forEach((g) => g.addEventListener("click", (e) => { const b = e.target.closest("button"); if (!b) return; const v = b.dataset.v; c[g.dataset.key] = /^\d+$/.test(v) ? +v : v; $$("button", g).forEach((x) => x.setAttribute("aria-pressed", x === b)); }));
    let rules = c.segment_rules.map((r) => [...r]);
    const drawRules = () => {
      $("#rules").innerHTML = rules.map(([n, r], i) => `<div class="rule"><span class="ord">${String(i + 1).padStart(2, "0")}</span>
        <input class="input" data-i="${i}" data-f="0" aria-label="Segment name" value="${esc(n)}"><input class="input cond" data-i="${i}" data-f="1" aria-label="Condition" value="${esc(r)}">
        <span class="cnt"></span><span class="mv"><button class="icon-btn" type="button" data-up="${i}" aria-label="Move up">↑</button><button class="icon-btn" type="button" data-down="${i}" aria-label="Move down">↓</button><button class="icon-btn" type="button" data-rm="${i}" aria-label="Remove">✕</button></span></div>`).join("");
    };
    drawRules();
    $("#rules").addEventListener("input", (e) => { const t = e.target.closest("[data-i]"); if (t) rules[+t.dataset.i][+t.dataset.f] = t.value; });
    $("#rules").addEventListener("click", (e) => {
      const up = e.target.closest("[data-up]"), dn = e.target.closest("[data-down]"), rm = e.target.closest("[data-rm]");
      if (up && +up.dataset.up > 0) { const i = +up.dataset.up; [rules[i - 1], rules[i]] = [rules[i], rules[i - 1]]; }
      else if (dn && +dn.dataset.down < rules.length - 1) { const i = +dn.dataset.down; [rules[i + 1], rules[i]] = [rules[i], rules[i + 1]]; }
      else if (rm) rules.splice(+rm.dataset.rm, 1); else return;
      drawRules();
    });
    $("#addRule").addEventListener("click", () => { rules.push(["", ""]); drawRules(); $$("#rules input")[rules.length * 2 - 2].focus(); });
    $("#resetRules").addEventListener("click", () => { rules = SPEC.default_rules.map((r) => [...r]); drawRules(); });

    async function save(run) {
      $("#err").hidden = true;
      const body = { ...c, snapshot_date: $("#dayAfter").checked ? null : $("#snap").value || null, lookback_months: +$("#lookback").value,
        reporting_currency: $("#cur").value.trim().toUpperCase(), frequency_bins: $("#fbins").value, baseline_run_id: $("#base").value || null,
        outlier_quantile: +$("#oq").value, reconciliation_tolerance_pct: +$("#tol").value, small_segment_threshold: +$("#small").value,
        segment_rules: rules.filter(([n, r]) => n.trim() || r.trim()) };
      try {
        const res = await api("PUT", `/api/runs/${RUN.meta.id}/config`, body);
        RUN.config = res.config;
        $("#warn").innerHTML = res.warnings.map((w) => banner("warn", "Note", w)).join("");
        if (run) { await api("POST", `/api/runs/${RUN.meta.id}/check`); go(`/runs/${RUN.meta.id}/check`); } else RFM.toast("Settings saved");
      } catch (e) { showError($("#err"), e); }
    }
    $("#save").addEventListener("click", () => save(false));
    $("#saveRun").addEventListener("click", () => save(true));
  }

  /* ---------------- job progress ---------------- */
  function progressView(kind, s, onDone) {
    view.innerHTML = head(kind === "check" ? "Step 03 · Data check" : "Step 04 · Report", kind === "check" ? "Checking the data…" : "Building the report…", "You can leave this page — the job keeps running and the result is saved with the run.") +
      `<div class="progress"><div class="row"><b id="pl">${esc(s.label || "Starting")}</b><span class="spacer"></span><span class="muted num" id="pp">${pct(s.progress || 0)}</span></div><div class="bar"><i id="pb" style="width:${(s.progress || 0) * 100}%"></i></div></div>`;
    const tick = async () => {
      try {
        const st = await api("GET", `/api/runs/${RUN.meta.id}/status`);
        if (st.state === "running") {
          $("#pl").textContent = st.label || ""; $("#pp").textContent = pct(st.progress); $("#pb").style.width = st.progress * 100 + "%";
          poll = setTimeout(tick, 1000);
        } else { RUN = await api("GET", `/api/runs/${RUN.meta.id}`); topbar(kind); onDone(st); }
      } catch (e) { poll = setTimeout(tick, 3000); }
    };
    poll = setTimeout(tick, 600);
  }
  const jobFailed = (s, kind) => s.state === "error" && s.job === kind ? errBox({ error: s.error }) : "";

  /* ---------------- 03 data check ---------------- */
  async function check() {
    const s = RUN.status;
    if (s.state === "running" && s.job === "check") return progressView("check", s, () => check());
    let data = null;
    try { data = await api("GET", `/api/runs/${RUN.meta.id}/check`); } catch (e) { /* none yet */ }
    const runBtn = `<button class="btn ${data ? "btn-secondary" : "btn-primary"}" id="runCheck">${data ? "Re-run check" : "Run data check"}</button>`;
    if (!data) {
      view.innerHTML = head("Step 03 · Data check", "Is the export fit for RFM?", "Reads the tables, cleans them and lists every issue it finds.", runBtn) + jobFailed(s, "check");
      $("#runCheck").addEventListener("click", startCheck); return;
    }
    const st = data.stats, v = data.verdict, opts = data.trim_options, trims = RUN.config.trims || {};
    view.innerHTML = head("Step 03 · Data check", "Is the export fit for RFM?", `Window ${esc(data.window_start)} → ${esc(data.window_end)} · amounts in ${esc(data.currency)}. Trim the dataset on the right if something looks off, then build the report.`,
      `${runBtn}<button class="btn btn-secondary" id="dlIssues">Download findings (CSV)</button><a class="btn btn-primary" href="/runs/${RUN.meta.id}/report" data-link>Build report →</a>`) +
      jobFailed(s, "check") + (RUN.stale?.check ? banner("info", "Settings changed since this check", "Re-run the check to see the effect.") : "") +
      banner(v.level === "critical" ? "crit" : v.level === "warning" ? "warn" : "ok", v.title, v.text) +
      data.warnings.map((w) => banner("info", "Note", w)).join("") +
      `<div class="layout-side"><div class="grid">
        <div class="grid g4">${stat("Rows received", compact(st.rows_received), "transactions table", "lp") + stat("Rows used", pct(st.rows_identified / st.rows_received, 1), `${compact(st.rows_identified)} clean and identified`, "hl") +
          stat("Customers", compact(st.customers), `${fmt(st.outliers)} flagged as outliers`) + stat("Critical issues", fmt(data.counts.critical), `${data.counts.warning} warnings · ${data.counts.info} info`, "lt")}</div>
        <div class="card"><div class="card-head"><div><h3>Transactions per month</h3><div class="sub">Look for gaps, spikes or a ramp-up that suggests a partial export.</div></div></div><div class="chart" id="cMonthly"></div></div>
        <div class="grid g2">
          <div class="card"><div class="card-head"><div><h3>Identifier coverage</h3><div class="sub">Share of purchases tied to a known customer</div></div></div><div class="chart" style="height:260px" id="cCov"></div></div>
          <div class="card"><div class="card-head"><div><h3>Order value distribution</h3><div class="sub">Net amount per purchase, ${esc(data.currency)}</div></div></div><div class="chart" style="height:260px" id="cAov"></div></div>
        </div>
        ${data.markets.length ? `<div class="card"><div class="card-head"><div><h3>Markets</h3><div class="sub">Rows and net revenue per market after cleaning and trims</div></div></div>${RFM.table([{ key: "market", label: "Market" }, { key: "rows", label: "Rows", num: 1, fmt: (x) => fmt(x) }, { key: "net_revenue", label: `Net revenue (${data.currency})`, num: 1, fmt: (x) => compact(x) }, { key: "currency", label: "Currencies" }], data.markets)}</div>` : ""}
        <div class="card"><div class="card-head"><div><h3>Checks with findings</h3><div class="sub">Sorted by severity. This is the table to share with the client when the export needs fixing.</div></div></div>
          ${RFM.table([{ key: "severity", label: "Severity", html: 1, fmt: (x) => RFM.sevPill(x) }, { key: "table", label: "Table" }, { key: "check", label: "Check" }, { key: "rows", label: "Rows", num: 1, fmt: (x) => fmt(x) }, { key: "share", label: "Share", num: 1, fmt: (x) => pct(x, 2) }, { key: "action", label: "What the app did" }], data.issues)}</div>
        <div class="card"><div class="card-head"><div><h3>Cleaning log</h3><div class="sub">Rows removed at each step, including your trims</div></div></div>
          ${RFM.table([{ key: "step", label: "Step" }, { key: "rows_before", label: "Rows before", num: 1, fmt: (x) => fmt(x) }, { key: "rows_after", label: "Rows after", num: 1, fmt: (x) => fmt(x) }, { key: "removed", label: "Removed", num: 1, fmt: (x) => fmt(x) }], data.cleaning_log)}</div>
        ${data.top_outliers.length ? `<div class="card"><div class="card-head"><div><h3>Top flagged customers</h3><div class="sub">Likely B2B, staff or test accounts</div></div></div>${RFM.table([{ key: "customer", label: "Customer (shortened)" }, { key: "purchases", label: "Purchases", num: 1, fmt: (x) => fmt(x) }, { key: "net_revenue", label: `Net revenue (${data.currency})`, num: 1, fmt: (x) => fmt(x) }, { key: "aov", label: "AOV", num: 1, fmt: (x) => fmt(x) }, { key: "days_since_purchase", label: "Days since purchase", num: 1 }], data.top_outliers)}</div>` : ""}
      </div>
      <aside class="card trim" aria-label="Trim the dataset">
        <div><h3>Trim the dataset</h3><div class="small muted">Applied after cleaning, before scoring. Every trim is logged in the report.</div></div>
        <div class="group"><span class="label">Date range</span><div class="grid g2" style="gap:8px">
          <select class="input" id="tFrom" aria-label="From month"><option value="">From the start</option>${opts.months.map((m) => `<option ${trims.date_from === m ? "selected" : ""}>${m}</option>`).join("")}</select>
          <select class="input" id="tTo" aria-label="To month"><option value="">To the end</option>${opts.months.map((m) => `<option ${trims.date_to === m ? "selected" : ""}>${m}</option>`).join("")}</select></div></div>
        ${chipGroup("Markets", "exclude_markets", opts.markets, trims)}${chipGroup("Channels", "exclude_mediums", opts.mediums, trims)}${opts.stores.length ? `<details class="group" ${(trims.exclude_stores || []).length ? "open" : ""}><summary class="label" style="cursor:pointer">Stores (${opts.stores.length})</summary>${chipGroup("", "exclude_stores", opts.stores, trims)}</details>` : ""}
        <div class="group"><span class="label">Purchase value (${esc(data.currency)})</span><div class="grid g2" style="gap:8px">
          <input class="input" id="tMin" type="number" placeholder="Min" aria-label="Minimum purchase value" value="${trims.min_amount ?? ""}"><input class="input" id="tMax" type="number" placeholder="Max" aria-label="Maximum purchase value" value="${trims.max_amount ?? ""}"></div>
          <span class="small muted">Typical range: ${fmt(opts.amount_quantiles["0.01"])} – ${fmt(opts.amount_quantiles["0.99"])} (1st–99th percentile)</span></div>
        <div class="group"><span class="label">Outliers (${fmt(st.outliers)})</span><div class="seg" id="tOut"><button aria-pressed="${RUN.config.outlier_action === "exclude"}" data-v="exclude">Exclude</button><button aria-pressed="${RUN.config.outlier_action === "keep"}" data-v="keep">Keep</button></div></div>
        ${data.trim_log.length ? `<div class="group"><span class="label">Trim log</span><div class="trim-log">${data.trim_log.map((t) => `<div><span>${esc(t.trim)}</span><b class="num">−${fmt(t.removed)}</b></div>`).join("")}<div class="muted"><span>Rows kept</span><b class="num">${fmt(st.rows_clean)}</b></div></div></div>` : ""}
        <div id="trimErr" hidden></div>
        <button class="btn btn-primary" id="applyTrim">Apply and re-check</button>
        ${Object.keys(trims).length ? '<button class="btn btn-ghost" id="clearTrim">Remove all trims</button>' : ""}
      </aside></div>`;
    $("#runCheck").addEventListener("click", startCheck);
    $("#dlIssues").addEventListener("click", () => downloadCsv(`data_check_${RUN.meta.id}.csv`, data.issues));
    $$("[data-chips]").forEach((g) => g.addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) b.setAttribute("aria-pressed", b.getAttribute("aria-pressed") !== "true"); }));
    $("#tOut").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) $$("button", $("#tOut")).forEach((x) => x.setAttribute("aria-pressed", x === b)); });
    $("#applyTrim").addEventListener("click", () => applyTrims(false));
    $("#clearTrim")?.addEventListener("click", () => applyTrims(true));
    drawCheckCharts(data);
  }
  function chipGroup(label, key, items, trims) {
    if (!items.length) return "";
    const ex = new Set(trims[key] || []);
    return `<div class="group">${label ? `<span class="label">${label}</span>` : ""}<div class="chips" data-chips="${key}">${items.map((o) => `<button class="chip" data-v="${esc(o.value)}" aria-pressed="${!ex.has(o.value)}" title="${fmt(o.rows)} rows">${esc(o.value)} · ${compact(o.rows)}</button>`).join("")}</div></div>`;
  }
  async function applyTrims(clear) {
    const trims = clear ? {} : {
      date_from: $("#tFrom").value || undefined, date_to: $("#tTo").value || undefined,
      min_amount: $("#tMin").value === "" ? undefined : +$("#tMin").value, max_amount: $("#tMax").value === "" ? undefined : +$("#tMax").value,
      ...Object.fromEntries($$("[data-chips]").map((g) => [g.dataset.chips, $$("button", g).filter((b) => b.getAttribute("aria-pressed") === "false").map((b) => b.dataset.v)])),
    };
    const outlier = $("[aria-pressed=true]", $("#tOut"))?.dataset.v || RUN.config.outlier_action;
    try {
      const res = await api("PUT", `/api/runs/${RUN.meta.id}/config`, { ...RUN.config, trims, outlier_action: outlier });
      RUN.config = res.config; await startCheck();
    } catch (e) { showError($("#trimErr"), e); }
  }
  async function startCheck() {
    try { const s = await api("POST", `/api/runs/${RUN.meta.id}/check`); progressView("check", s, () => check()); }
    catch (e) { view.insertAdjacentHTML("afterbegin", errBox(e)); }
  }
  function drawCheckCharts(d) {
    const mv = d.monthly_volume, labels = mv.map((r) => r.month);
    RFM.chart($("#cMonthly"), { ...base(), tooltip: { ...base().tooltip, trigger: "axis", axisPointer: shadow, valueFormatter: (v) => fmt(v) + " rows" },
      xAxis: axis({ type: "category", data: labels, axisLabel: { color: T.muted, fontSize: 11.5, formatter: (v) => (v.slice(5) === "01" || v === labels[0] ? v : v.slice(5)) } }),
      yAxis: axis({ type: "value", axisLabel: { color: T.muted, formatter: (v) => compact(v) } }),
      series: [["Identified", mv.map((r) => r.identified), T.blue], ["Anonymous", mv.map((r) => r.rows - r.identified), T.sky]].map(([n, data, c], i) => ({ name: n, type: "bar", stack: "m", data, barMaxWidth: 26, color: c, itemStyle: { borderColor: "#fff", borderWidth: 1, borderRadius: i ? [4, 4, 0, 0] : 0 } })) });
    const cov = Object.entries(d.coverage).filter(([k]) => k !== "Revenue identified");
    RFM.chart($("#cCov"), { ...base(), legend: { show: false }, grid: { left: 8, right: 48, top: 8, bottom: 8, containLabel: true },
      tooltip: { ...base().tooltip, trigger: "item", formatter: (p) => `${esc(p.name)}<br><b>${pct(p.value)}</b>` },
      xAxis: axis({ type: "value", max: 1, axisLabel: { show: false }, splitLine: { show: false } }),
      yAxis: axis({ type: "category", data: cov.map((c) => c[0]), axisLine: { show: false }, axisLabel: { color: T.navy, fontSize: 12.5 } }),
      series: [{ type: "bar", data: cov.map((c) => c[1]), barWidth: 20, itemStyle: { color: T.blue, borderRadius: [0, 4, 4, 0] }, showBackground: true, backgroundStyle: { color: T.lp, borderRadius: 4 },
        label: { show: true, position: "right", color: T.navy, fontWeight: 600, formatter: (p) => pct(p.value) } }] });
    const dist = d.order_value_distribution, lab = dist.map((b) => (b.to == null ? `${compact(b.from)}+` : `${compact(b.from)}–${compact(b.to)}`));
    RFM.chart($("#cAov"), { ...base(), legend: { show: false }, tooltip: { ...base().tooltip, trigger: "axis", axisPointer: shadow, formatter: (p) => `${p[0].name}<br><b>${pct(p[0].value, 1)}</b> of purchases (${fmt(dist[p[0].dataIndex].purchases)})` },
      xAxis: axis({ type: "category", data: lab }), yAxis: axis({ type: "value", axisLabel: { color: T.muted, formatter: (v) => pct(v) } }),
      series: [{ type: "bar", data: dist.map((b) => b.share), barMaxWidth: 34, itemStyle: { color: T.blue, borderRadius: [4, 4, 0, 0] } }] });
  }
  function downloadCsv(name, rows) {
    if (!rows.length) return;
    const cols = Object.keys(rows[0]), q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const blob = new Blob([[cols.join(","), ...rows.map((r) => cols.map((c) => q(r[c])).join(","))].join("\n")], { type: "text/csv" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  /* ---------------- 04 report ---------------- */
  async function report() {
    const s = RUN.status;
    if (s.state === "running" && s.job === "report") return progressView("report", s, () => report());
    let data = null;
    try { data = await api("GET", `/api/runs/${RUN.meta.id}/report`); } catch (e) { /* none yet */ }
    if (!data) {
      view.innerHTML = head("Step 04 · Report", "Build the report", "Scores every customer, assigns segments and builds the cohorts and market comparison. Takes about a minute for large clients.", '<button class="btn btn-primary" id="build">Build report</button>') + jobFailed(s, "report");
      $("#build").addEventListener("click", startReport); return;
    }
    view.innerHTML = jobFailed(s, "report") + (RUN.stale?.report ? banner("info", "Settings or data changed since this report was built", "Rebuild to include the changes.") : "") +
      `<div id="shareBox" class="share-box" hidden></div><div id="reportRoot"></div>`;
    const actions = `<button class="btn btn-secondary" id="rebuild">Rebuild</button><a class="btn btn-secondary" href="/api/runs/${RUN.meta.id}/download.zip">Download all (ZIP)</a><button class="btn btn-primary" id="share">Share link</button>`;
    RFM.Report.mount($("#reportRoot"), data, { actionsHtml: actions });
    $("#rebuild").addEventListener("click", startReport);
    $("#share").addEventListener("click", () => shareBox(true));
    if ((RUN.meta.shares || []).length) shareBox(false);
  }
  async function startReport() {
    try { const s = await api("POST", `/api/runs/${RUN.meta.id}/report`); progressView("report", s, () => report()); }
    catch (e) { view.insertAdjacentHTML("afterbegin", errBox(e)); }
  }
  async function shareBox(create) {
    const box = $("#shareBox"); box.hidden = false;
    if (create) {
      try { const s = await api("POST", `/api/runs/${RUN.meta.id}/share`, { days: 90 }); RUN = await api("GET", `/api/runs/${RUN.meta.id}`); RFM.toast("Share link created"); box.dataset.latest = s.url; }
      catch (e) { box.innerHTML = errBox(e); return; }
    }
    const base = (await api("GET", "/api/share-base")).base;
    const shares = (RUN.meta.shares || []).slice().reverse();
    box.innerHTML = `<div class="card-head"><div><h3>Shared links</h3><div class="sub">Anyone with a link can view the report as it was when shared — no login. Links expire; remove one to stop access.</div></div></div>` +
      shares.map((s) => `<div class="share-url"><input class="input" readonly value="${esc(base)}/r/${esc(s.token)}" aria-label="Share link"><button class="btn btn-secondary btn-sm" data-copy>Copy</button>
        <a class="btn btn-ghost btn-sm" href="${esc(base)}/r/${esc(s.token)}" target="_blank" rel="noopener">Open</a><button class="btn btn-ghost btn-sm" data-revoke="${esc(s.token)}">Remove</button>
        <span class="small muted">expires ${RFM.date(s.expires_at)}</span></div>`).join("");
    box.onclick = async (e) => {
      const c = e.target.closest("[data-copy]"), r = e.target.closest("[data-revoke]");
      if (c) { const inp = c.previousElementSibling; try { await navigator.clipboard.writeText(inp.value); RFM.toast("Link copied"); } catch { inp.select(); } }
      if (r) { await api("DELETE", `/api/runs/${RUN.meta.id}/share/${r.dataset.revoke}`); RUN = await api("GET", `/api/runs/${RUN.meta.id}`); RFM.toast("Link removed"); shareBox(false); }
    };
  }

  route();
})();
