/* MuleRing Shield dashboard. Plain JavaScript, no build step.
   The page never polls: the server pushes events and the page updates in place. */
(() => {
  "use strict";
  const $ = (sel, root = document) => root.querySelector(sel);
  const app = $("#app");
  const BANK_COLOR = { A: "#2a78d6", B: "#eb6834", C: "#1baf7a" };
  const S = {
    session: null,            // { role, role_name, roles, code_required, demo_controls }
    truth: localStorage.getItem("mrs.truth") === "1",
    alertFilter: "all", search: "", caseFilter: "open",
    attackMin: window.matchMedia("(max-width: 720px)").matches,   // start compact on a phone
    cy: null, graphSig: "",
    es: null, connected: false, running: true, speed: 60, clock: null, version: 0,
    activity: [], ledger: [], attack: null, attackDismissed: 0,
    hooks: {},                // { tick(p), event(p) } set by the page that is open
    seen: new Set(),          // alert tokens already shown, so only arrivals flash
    renderId: 0,
  };

  // ------------------------------------------------------------ helpers
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const num = (n) => Number(n).toLocaleString("en-IN");
  const inr = (n) => "₹" + Math.round(n).toLocaleString("en-IN");
  const inrShort = (n) => n >= 1e7 ? `₹${(n / 1e7).toFixed(2)} Cr` : n >= 1e5 ? `₹${(n / 1e5).toFixed(1)} L` : inr(n);
  const pct = (x, d = 0) => (x * 100).toFixed(d) + "%";
  const when = (iso) => new Date(iso).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
  const hhmm = (iso) => new Date(iso).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false });
  const plural = (n, one, many) => `${num(n)} ${n === 1 ? one : (many || one + "s")}`;
  const truthQs = () => (S.truth && S.session.demo_controls ? "?truth=1" : "");
  const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

  async function api(path, options = {}) {
    const res = await fetch(path, {
      ...options, credentials: "same-origin",
      headers: { "X-Requested-With": "MuleRingShield", ...(options.body ? { "Content-Type": "application/json" } : {}) },
    });
    if (res.status === 401 && path !== "/api/session") { signedOut(); throw new Error("Please sign in."); }
    if (!res.ok) {
      let detail = res.statusText;
      try { const j = await res.json(); detail = typeof j.detail === "string" ? j.detail : "The request was not accepted."; } catch (_) { /* not JSON */ }
      throw new Error(detail);
    }
    return res.status === 204 ? null : res.json();
  }
  const send = (method, path, body) => api(path, { method, body: body ? JSON.stringify(body) : undefined });

  function toast(html, ms = 8000) {
    const el = $("#toast");
    el.innerHTML = `<button class="close" aria-label="Dismiss">×</button>${html}`;
    el.hidden = false;
    $(".close", el).onclick = () => { el.hidden = true; };
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { el.hidden = true; }, ms);
  }

  const bankChip = (code, name) => `<span class="chip bank" style="--c:${BANK_COLOR[code] || "#888"}">${esc(name)}</span>`;
  const bandWord = { high: "High risk", medium: "Watch", low: "Low risk" };
  const bandChip = (band) => `<span class="chip ${band}">${bandWord[band]}</span>`;
  const statusChip = (s) => ({
    held: `<span class="chip status-held">On hold</span>`,
    confirmed: `<span class="chip status-confirmed">Confirmed mule</span>`,
    cleared: `<span class="chip status-cleared">Cleared</span>`,
    open: `<span class="chip">Open</span>`,
  }[s] || "");
  const roleName = (r) => ({ L1: "collector", L2: "mover", CASHOUT: "cash-out" }[r] || r);
  const truthChip = (t) => !t ? "" :
    `<span class="chip truth" title="Synthetic ground-truth label">${t.is_mule ? "Label: mule" + (t.role ? ` (${esc(roleName(t.role))})` : "") : "Label: legitimate"}</span>`;
  const scoreBox = (c) => c.score === undefined
    ? bandChip(c.band)
    : `<span class="score-box ${c.band}" title="${bandWord[c.band]}">${c.score}</span>`;
  const idLine = (c) => c.own
    ? `<span class="muted small">${esc(c.account)} · ${esc(c.vpa)}</span>`
    : `<span class="muted small mono">${esc(c.token)}</span>`;
  const isHub = () => S.session.role === "hub";
  const prioChip = (p) => `<span class="chip ${p === "high" ? "high" : p === "medium" ? "medium" : ""}">${{ high: "High", medium: "Medium", low: "Low" }[p]} priority</span>`;
  const caseStateChip = (s) => s === "open" ? `<span class="chip share">Case open</span>` : `<span class="chip">Case closed</span>`;
  const paintCaseCount = async (list) => {
    try {
      const all = list || await api("/api/cases");
      $("#count-cases").textContent = all.filter((c) => c.state === "open").length;
    } catch (_) { /* decorative */ }
  };
  const refreshCaseCount = debounce(() => paintCaseCount(), 600);

  // ------------------------------------------------------------ sign-in
  const ROLE_BLURB = {
    hub: "Sees tokens, scores and rings across all banks. No customer identities. Read-only.",
    A: "Sees Bank A's customers in full, and acts on their alerts.",
    B: "Sees Bank B's customers in full, and acts on their alerts.",
    C: "Sees Bank C's customers in full, and acts on their alerts.",
  };
  function showSignIn(info, message) {
    S.session = null;
    disconnect();
    $("#controls").hidden = true; $("#tabs").hidden = true; $("#attack-panel").hidden = true;
    app.innerHTML = `
      <div class="signin card">
        <h1>Sign in</h1>
        <p class="sub">Choose who you are. What you can see and do depends on your role, and it is enforced on the server.</p>
        ${message ? `<div class="notice warn" role="alert">${esc(message)}</div>` : ""}
        ${info.code_required ? `<label class="field"><span>Access code</span>
          <input id="code" type="password" autocomplete="off" maxlength="200" placeholder="Given to you by the team"></label>` : ""}
        <div class="role-grid">${info.roles.map((r) => `
          <button class="role-card" data-role="${esc(r.code)}">
            <b><i style="background:${BANK_COLOR[r.code] || "#1f3a5f"}"></i>${esc(r.name)}</b>
            <span>${esc(ROLE_BLURB[r.code] || "")}</span>
          </button>`).join("")}</div>
        <p class="muted small" style="margin-top:16px">This is a demonstration with synthetic data. A production system would use each bank's single sign-on with multi-factor authentication.</p>
      </div>`;
    app.querySelectorAll(".role-card").forEach((b) => {
      b.onclick = async () => {
        try {
          const code = $("#code") ? $("#code").value : "";
          startApp(await send("POST", "/api/session", { role: b.dataset.role, code }));
        } catch (err) { showSignIn(info, err.message); }
      };
    });
    if ($("#code")) $("#code").focus();
  }
  async function signedOut() {
    if (!S.session) return;
    try { showSignIn(await api("/api/session"), "Your session has ended. Please sign in again."); } catch (_) { /* offline */ }
  }

  // ------------------------------------------------------------ live connection
  function connect() {
    disconnect();
    const es = new EventSource("/api/events");
    S.es = es;
    es.onopen = () => { S.connected = true; paintLive(); };
    es.onmessage = (m) => { try { onEvent(JSON.parse(m.data)); } catch (_) { /* ignore a malformed frame */ } };
    es.onerror = async () => {
      S.connected = false; paintLive();
      if (es.readyState === EventSource.CLOSED) {   // the server refused: probably signed out
        try { const info = await api("/api/session"); if (!info.role) showSignIn(info, "Your session has ended. Please sign in again."); else setTimeout(connect, 3000); } catch (_) { setTimeout(connect, 5000); }
      }
    };
  }
  function disconnect() { if (S.es) { S.es.close(); S.es = null; } S.connected = false; }

  function paintLive() {
    const el = $("#live");
    const state = !S.connected ? "off" : S.running ? "on" : "paused";
    el.className = "live " + state;
    $("#live-text").textContent = !S.connected ? "Reconnecting…"
      : `${S.running ? "Live" : "Paused"}${S.clock ? " · " + when(S.clock) : ""} · ${S.speed}×`;
    el.title = S.session.demo_controls
      ? (S.running ? "Live stream is running. Click to pause." : "Live stream is paused. Click to resume.")
      : "Live stream status";
  }
  function paintCounts(k) {
    $("#count-alerts").textContent = k.alerts;
    $("#count-rings").textContent = k.rings;
  }

  function onEvent(p) {
    if (p.clock) S.clock = p.clock;
    if (p.type === "tick") {
      S.version = p.version; S.running = p.running; S.speed = p.speed;
      paintLive(); paintCounts(p.kpis);
      if (p.ledger && p.ledger.length) { S.ledger = p.ledger.slice().reverse().concat(S.ledger).slice(0, 40); }
      if (S.hooks.tick) S.hooks.tick(p);
      return;
    }
    if (p.type === "attack") { S.attack = p; paintAttack(); return; }
    if (S.activity.some((a) => a.id === p.id)) return;
    S.activity.unshift(p); S.activity.length = Math.min(S.activity.length, 60);
    paintActivity(true);
    if (p.type === "system") {
      if (p.what === "stream") { S.running = /resumed/.test(p.text); paintLive(); }
      if (p.what === "reset") { S.attack = null; S.ledger = []; S.seen.clear(); paintAttack(); render(); return; }
    }
    if (p.type === "alert" && p.kind === "new") queueAlertToast(p);
    if (p.type === "ring" && p.kind === "new") toast(`<b>New ring detected</b>${esc(p.text)}<br><a href="#/rings/${esc(p.ring)}">Open the ring</a>`);
    if (p.type === "case") refreshCaseCount();
    if (S.hooks.event) S.hooks.event(p);
  }

  const pendingAlerts = [];
  const flushAlertToast = debounce(() => {
    const list = pendingAlerts.splice(0);
    if (!list.length) return;
    if (list.length === 1) {
      const p = list[0];
      toast(`<b>New alert</b>${esc(p.text)}${p.detail ? `<br><span style="opacity:.8">${esc(p.detail)}</span>` : ""}
        <br><a href="#/alerts/${esc(p.token)}">Open the account</a>`);
    } else {
      toast(`<b>${list.length} new alerts</b>${esc(list[0].text)}, and ${list.length - 1} more.<br><a href="#/alerts" data-new="1">See the new alerts</a>`);
      const link = $("#toast a[data-new]");
      if (link) link.onclick = () => { S.alertFilter = "new"; if (location.hash === "#/alerts") render(); };
    }
  }, 1200);
  function queueAlertToast(p) { pendingAlerts.push(p); flushAlertToast(); }

  function activityHtml() {
    if (!S.activity.length) return `<p class="empty">Nothing has happened yet. Events appear here the moment they occur.</p>`;
    return `<ul class="feed">${S.activity.slice(0, 40).map((a, i) => {
      const href = a.token ? `#/alerts/${esc(a.token)}` : a.ring ? `#/rings/${esc(a.ring)}` : "";
      return `<li class="${a.level === "high" ? "high" : ""} ${i === 0 && activityHtml.fresh ? "fresh" : ""}">
        <span class="mark"></span>
        <span>${href ? `<a href="${href}">${esc(a.text)}</a>` : esc(a.text)}
          ${a.shared ? ` <span class="chip share">Found through sharing</span>` : ""}
          ${a.detail ? `<div class="d">${esc(a.detail)}</div>` : ""}</span>
        <span class="t">${a.clock ? hhmm(a.clock) : ""}</span></li>`;
    }).join("")}</ul>`;
  }
  function paintActivity(fresh) {
    const el = $("#activity");
    if (!el) return;
    activityHtml.fresh = fresh;
    el.innerHTML = activityHtml();
    activityHtml.fresh = false;
  }

  function paintAttack() {
    const el = $("#attack-panel"), a = S.attack;
    if (!a || !S.session || a.n === S.attackDismissed) { el.hidden = true; return; }
    const ringLink = a.ring ? `<a href="#/rings/${esc(a.ring)}">Open ${esc(a.ring)}</a>` : `<span class="muted">not yet linked into a ring</span>`;
    el.className = "attack-panel" + (a.done ? " done" : "") + (S.attackMin ? " min" : "");
    el.hidden = false;
    if (S.attackMin) {
      el.innerHTML = `<button class="expand" aria-label="Show attack details">
        <b>${a.done ? "Attack finished" : "Attack in progress"}</b> · ${a.executed}/${a.total} payments · ${a.flagged} of ${a.members} flagged</button>`;
      $(".expand", el).onclick = () => { S.attackMin = false; paintAttack(); };
      return;
    }
    el.innerHTML = `
      <h4>${a.done ? "Simulated attack finished" : "Simulated attack in progress"}
        <span><button class="x" data-min aria-label="Minimise">–</button><button class="x" data-close aria-label="Dismiss">×</button></span></h4>
      <div class="muted small">${a.members} mule accounts across ${a.banks.length} banks</div>
      <div class="row"><span>Payments made</span><b>${a.executed} of ${a.total}</b></div>
      <div class="progress"><i style="width:${a.executed / a.total * 100}%"></i></div>
      <div class="row"><span>Mule accounts flagged</span><b>${a.flagged} of ${a.members}</b></div>
      <div class="progress good"><i style="width:${a.flagged / a.members * 100}%"></i></div>
      <div class="row"><span>First alert</span><b>${a.first_alert_min === null ? "waiting…" : `after ${a.first_alert_min} min`}</b></div>
      ${a.paid_before_alert !== null ? `<div class="muted small" style="margin-top:4px">Victims had paid in ${inr(a.paid_before_alert)} of ${inr(a.planned)} by then. The rest came after the ring was already flagged.</div>` : ""}
      <div class="row"><span>Ring</span>${ringLink}</div>`;
    $("[data-close]", el).onclick = () => { S.attackDismissed = a.n; el.hidden = true; };
    $("[data-min]", el).onclick = () => { S.attackMin = true; paintAttack(); };
  }

  // ------------------------------------------------------------ routing
  const routes = { overview, alerts, cases, rings, privacy, audit };
  async function render() {
    if (!S.session) return;
    const id = ++S.renderId;
    const [, tab = "overview", arg] = location.hash.split("/");
    const page = routes[tab] ? tab : "overview";
    document.querySelectorAll("#tabs a").forEach((a) => a.classList.toggle("active", a.dataset.tab === page));
    if (S.cy) { S.cy.destroy(); S.cy = null; }
    S.hooks = {}; S.graphSig = "";
    $("#tooltip").hidden = true;
    try {
      await routes[page](arg && decodeURIComponent(arg), id);
    } catch (err) {
      if (id !== S.renderId || !S.session) return;
      app.innerHTML = `<div class="card"><h2>That did not load</h2><p class="sub">${esc(err.message)}</p>
        <a href="#/overview">Back to overview</a></div>`;
    }
  }
  const stale = (id) => id !== S.renderId;

  // ------------------------------------------------------------ overview
  const kpi = (key, label, value, hint, highlight) => `
    <div class="kpi ${highlight ? "highlight" : ""}"><div class="label">${esc(label)}</div>
      <div class="value" data-kpi="${key}">${esc(value)}</div><div class="hint" data-hint="${key}">${esc(hint || "")}</div></div>`;
  const kpiText = (k, days) => ({
    accounts: [num(k.accounts), `${num(k.transactions)} transactions in ${days} days`],
    alerts: [num(k.alerts), k.new_alerts ? `${k.new_alerts} new in the last 10 minutes` : `${k.held} on hold for review`],
    rings: [num(k.rings), isHub() ? "groups of linked flagged accounts" : "involving your accounts"],
    funnelled: [inrShort(k.funnelled), "paid in from outside while rings were active"],
    only_consortium: [num(k.only_consortium), "alerts a bank's own model would have missed"],
    speed: [Math.round(k.score_seconds * 1000) + " ms", `to rescore all ${num(k.scored_accounts)} accounts`],
  });

  async function overview(_, id) {
    const o = await api("/api/overview");
    if (stale(id)) return;
    const b = o.benchmark, hub = isHub(), days = o.window_days;
    const best = b.detectors.reduce((a, d) => (d.f1 > a.f1 ? d : a));
    const cons = b.detectors.find((d) => d.key === "consortium");
    const local = b.detectors.find((d) => d.key === "local");
    const t = kpiText(o.kpis, days);
    app.innerHTML = `
      <h1>${hub ? "Consortium overview" : esc(o.role_name.replace(" analyst", "")) + " overview"}</h1>
      <p class="sub">${hub
        ? "The hub sees tokens, risk scores and aggregated flows from every member bank. It never sees a customer's name or account number."
        : "You see your own customers in full. Accounts at other banks appear only as a token and a risk band."}
        Everything on this page updates by itself as transactions arrive.</p>
      <div class="kpis">
        ${kpi("accounts", "Accounts monitored", ...t.accounts)}
        ${kpi("alerts", "Open alerts", ...t.alerts)}
        ${kpi("rings", "Suspected rings", ...t.rings)}
        ${kpi("funnelled", "Money collected by rings", ...t.funnelled)}
        ${kpi("only_consortium", "Found only through sharing", ...t.only_consortium, true)}
        ${kpi("speed", "Scoring speed", ...t.speed)}
      </div>

      <div class="section grid cols-2">
        <div class="card">
          <h2>Live activity</h2>
          <p class="sub">New alerts, rings and analyst decisions, as they happen.</p>
          <div id="activity">${activityHtml()}</div>
        </div>
        ${hub ? `<div class="card">
          <h2>Member banks</h2>
          <p class="sub">The hub receives tokens and totals from each bank, never single transactions.</p>
          <div class="table-wrap"><table>
            <thead><tr><th>Bank</th><th class="r">Accounts</th><th class="r">Open alerts</th><th class="r">Found through sharing</th><th class="r">Rings</th></tr></thead>
            <tbody id="banks">${banksHtml(o.banks)}</tbody>
          </table></div></div>`
        : `<div class="card">
          <h2>Live ledger</h2>
          <p class="sub">Your bank's own transactions as they are booked. They never leave the bank.</p>
          <div id="ledger">${ledgerHtml()}</div></div>`}
      </div>

      <div class="section card">
        <h2>Does sharing intelligence catch more mules?</h2>
        <p class="sub">Three detectors scored the same accounts. Share of mule accounts each one caught:</p>
        ${b.detectors.map((d) => `
          <div class="metric-row ${d.key === best.key ? "best" : ""}">
            <div class="name">${esc(d.label)}</div>
            <div class="bar" title="${pct(d.recall)} of mule accounts caught"><i style="width:${d.recall * 100}%"></i></div>
            <div class="val num"><b>${pct(d.recall)}</b> · ${num(d.tp)} of ${num(b.mules)}</div>
          </div>`).join("")}
        <div class="table-wrap" style="margin-top:10px">
          <table>
            <thead><tr><th>Detector</th><th class="r">Alerts raised</th><th class="r">Real mules</th>
              <th class="r">False alarms</th><th class="r">Mules missed</th><th class="r">Precision</th><th class="r">Recall</th></tr></thead>
            <tbody>${b.detectors.map((d) => `<tr>
              <td>${esc(d.label)}</td><td class="r num">${num(d.alerts)}</td><td class="r num">${num(d.tp)}</td>
              <td class="r num">${num(d.fp)}</td><td class="r num">${num(d.fn)}</td>
              <td class="r num">${pct(d.precision, 1)}</td><td class="r num">${pct(d.recall, 1)}</td></tr>`).join("")}</tbody>
          </table>
        </div>
        <div class="callouts">
          <div class="callout"><b>${num(b.extra_mules_from_sharing)}</b><span>mule accounts caught only because banks shared intelligence</span></div>
          <div class="callout"><b>${num(local.fp - cons.fp)} fewer</b><span>false alarms than a single bank working alone (${num(cons.fp)} vs ${num(local.fp)})</span></div>
          <div class="callout"><b>${num(b.rings_recovered)} of ${num(b.rings_true)}</b><span>rings pieced back together across banks</span></div>
          <div class="callout"><b>${pct(b.max_single_bank_share)}</b><span>of a ring's accounts sit at the best-placed single bank, on average</span></div>
        </div>
        <p class="muted small" style="margin-top:14px">Measured on ${b.worlds} synthetic test datasets the models never saw during training
          (${num(b.accounts)} accounts, ${num(b.mules)} mule accounts). Synthetic data is cleaner than real banking data, so treat these
          figures as a comparison between approaches, not as a forecast of production accuracy.</p>
      </div>

      <div class="section grid cols-2">
        <div class="card">
          <h2>Who gets wrongly flagged?</h2>
          <p class="sub">Legitimate customers flagged by each detector. Freezing an innocent account does real harm, so this is tracked by customer group.</p>
          <div class="table-wrap"><table>
            <thead><tr><th>Legitimate customers</th><th class="r">Accounts</th><th class="r">Rules</th><th class="r">Single bank</th><th class="r">Consortium</th></tr></thead>
            <tbody>${b.fairness.map((f) => `<tr><td>${esc(f.segment)}</td><td class="r num">${num(f.accounts)}</td>
              <td class="r num">${num(f.rules)}</td><td class="r num">${num(f.local)}</td><td class="r num"><b>${num(f.consortium)}</b></td></tr>`).join("")}</tbody>
          </table></div>
        </div>
        <div class="card">
          <h2>What the model weighs most</h2>
          <p class="sub">How far each factor can move a risk score. The model is additive, so these weights are exact, not estimates.</p>
          ${o.model.top_features.map((f) => `
            <div class="metric-row feat">
              <div>${esc(f.label)}</div>
              <div class="bar thin"><i style="width:${f.weight / o.model.top_features[0].weight * 100}%"></i></div>
              <div class="val">${f.source === "consortium" ? `<span class="chip share">Consortium</span>` : `<span class="chip own">Own-bank data</span>`}</div>
            </div>`).join("")}
        </div>
      </div>

      <div class="section card">
        <h2>How it was built</h2>
        <p class="sub" style="max-width:none">Gradient-boosted decision stumps (an additive, glass-box model) trained on
          ${num(o.model.train_accounts)} synthetic accounts with ${num(o.model.train_mules)} labelled mules, using ${o.model.features} features.
          Alert threshold ${Math.round(o.model.threshold * 100)} out of 100, chosen on a separate validation dataset.
          Training took ${o.model.seconds} s at start-up. The simulation clock runs ${S.speed} times faster than real time.</p>
        <h3>Rules baseline used for comparison</h3>
        <ul class="small" style="margin:0;padding-left:18px;color:var(--ink-2)">${o.rules.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>
      </div>`;

    S.hooks.tick = (p) => {
      const tt = kpiText(p.kpis, days);
      for (const key of Object.keys(tt)) {
        const v = $(`[data-kpi="${key}"]`, app), h = $(`[data-hint="${key}"]`, app);
        if (v && v.textContent !== tt[key][0]) v.textContent = tt[key][0];
        if (h && h.textContent !== tt[key][1]) h.textContent = tt[key][1];
      }
      const led = $("#ledger"); if (led && p.ledger && p.ledger.length) led.innerHTML = ledgerHtml();
    };
    if (hub) {
      S.hooks.event = debounce(async (p) => {
        if (stale(id) || !["alert", "ring", "case"].includes(p.type)) return;
        try { const fresh = await api("/api/overview"); if (!stale(id) && $("#banks")) $("#banks").innerHTML = banksHtml(fresh.banks); } catch (_) { /* next event retries */ }
      }, 400);
    }
  }
  const banksHtml = (banks) => banks.map((x) => `<tr><td>${bankChip(x.code, x.name)}</td><td class="r num">${num(x.accounts)}</td>
    <td class="r num">${num(x.alerts)}</td><td class="r num">${num(x.only_consortium)}</td><td class="r num">${num(x.rings)}</td></tr>`).join("");
  const ledgerHtml = () => !S.ledger.length
    ? `<p class="empty">Waiting for the next transaction…</p>`
    : `<ul class="ledger">${S.ledger.map((t) => `<li><span class="t">${hhmm(t.time)}</span>
        <span>${esc(t.from)} → ${esc(t.to)}</span><span class="num">${inr(t.amount)} <span class="muted">${esc(t.channel)}</span></span></li>`).join("")}</ul>`;

  // ------------------------------------------------------------ alerts
  const FILTERS = [["all", "All"], ["new", "New"], ["share", "Found only through sharing"], ["held", "On hold"], ["open", "Not yet reviewed"]];
  const inFilter = (a, key) => ({ all: true, new: a.is_new, share: a.only_consortium, held: a.status === "held", open: a.status === "open" }[key]);

  async function alerts(token, id) {
    if (token) return accountDetail(token, id);
    const hub = isHub();
    app.innerHTML = `
      <h1>Alerts</h1>
      <p class="sub">${hub ? "Every flagged account across the consortium, identified by token only."
        : "Flagged accounts held at your bank, highest risk first. Open one to see why it was flagged and to act on it."}
        New alerts appear here by themselves.</p>
      <div class="toolbar">
        <span id="filters"></span>
        <input class="search" type="search" placeholder="Search name, token or ring" value="${esc(S.search)}" aria-label="Search alerts">
      </div>
      <div class="card" style="padding:6px 8px"><div class="table-wrap"><table class="stack">
        <thead><tr><th>Risk</th><th>Account</th>${hub ? "<th>Bank</th>" : ""}<th title="Score from the bank's own model, then the score with consortium intelligence">Alone → shared</th>
          <th>Main reason</th><th>Ring</th><th>Status</th></tr></thead>
        <tbody id="alert-rows"><tr><td colspan="7" class="muted" style="padding:24px">Loading…</td></tr></tbody>
      </table></div></div>`;
    let list = [];
    const paint = () => {
      $("#filters").innerHTML = FILTERS.map(([key, label]) => `<button class="filter" data-filter="${key}" aria-pressed="${S.alertFilter === key}">${label}
        <span class="muted">${list.filter((a) => inFilter(a, key)).length}</span></button>`).join(" ");
      app.querySelectorAll(".filter").forEach((b) => { b.onclick = () => { S.alertFilter = b.dataset.filter; paint(); }; });
      const rows = list.filter((a) => inFilter(a, S.alertFilter) && (!S.search || [a.label, a.token, a.ring, a.account, a.top_reason]
        .some((v) => (v || "").toLowerCase().includes(S.search))));
      const first = S.seen.size === 0;
      $("#alert-rows").innerHTML = rows.map((a) => `
        <tr class="clickable ${!first && !S.seen.has(a.token) ? "fresh" : ""}" tabindex="0" data-token="${esc(a.token)}">
          <td class="lead">${scoreBox(a)}</td>
          <td class="lead"><b>${esc(a.label)}</b> ${a.is_new ? `<span class="chip new">New</span>` : ""} ${truthChip(a.truth)}<br>${idLine(a)}</td>
          ${hub ? `<td data-label="Bank">${bankChip(a.bank, a.bank_name)}</td>` : ""}
          <td data-label="Alone → shared" class="arrow-pair"><span>${a.local_score}<span class="to">→</span><b>${a.score}</b>
            ${a.only_consortium ? ` <span class="chip share">Found through sharing</span>` : ""}</span></td>
          <td data-label="Main reason" style="max-width:360px">${esc(a.top_reason)}</td>
          <td data-label="Ring">${a.ring ? `<a href="#/rings/${esc(a.ring)}" class="mono">${esc(a.ring)}</a>` : `<span class="muted">none</span>`}</td>
          <td data-label="Status"><span>${statusChip(a.status)} ${a.case ? caseStateChip(a.case.state) : ""}</span></td>
        </tr>`).join("") || `<tr><td colspan="7" class="muted lead" style="padding:24px">No alerts match this filter.</td></tr>`;
      list.forEach((a) => S.seen.add(a.token));
      bindRows();
    };
    const load = async () => {
      const fresh = await api("/api/alerts" + truthQs());
      if (stale(id)) return;
      list = fresh; paint();
    };
    const search = $(".search", app);
    search.oninput = () => { S.search = search.value.trim().toLowerCase(); paint(); };
    await load();
    const reload = debounce(() => load().catch(() => {}), 350);
    S.hooks.event = (p) => { if (["alert", "case", "ring"].includes(p.type)) reload(); };
  }
  function bindRows(root = app) {
    root.querySelectorAll("tr[data-token]").forEach((tr) => {
      const go = (ev) => { if (!ev.target.closest("a")) location.hash = `#/alerts/${tr.dataset.token}`; };
      tr.onclick = go;
      tr.onkeydown = (ev) => { if (ev.key === "Enter") go(ev); };
    });
  }

  // ------------------------------------------------------------ account detail
  const detailSig = (d) => JSON.stringify([d.score, d.local_score, d.band, d.status, d.ring, d.is_new,
    d.flows.map((f) => [f.amount, f.party.band]), (d.transactions || []).length && d.transactions[0].time, d.history.length,
    d.reasons.raises.map((r) => r.text)]);
  const graphSig = (g) => g.nodes.length + ":" + g.edges.length + ":" + g.edges.reduce((a, e) => a + e.amount, 0) + ":" + g.nodes.filter((n) => n.flagged).length;

  function headHtml(d) {
    const own = Math.max(d.impact_own, 0), cons = Math.max(d.impact_consortium, 0), total = own + cons || 1;
    return `
      <div class="acct-head">
        <div class="who">
          <h1>${esc(d.label)} ${d.is_new ? `<span class="chip new">New</span>` : ""} ${truthChip(d.truth)}</h1>
          <div>${idLine(d)}</div>
          <p style="margin:10px 0 0">${bankChip(d.bank, d.bank_name)} ${bandChip(d.band)} ${statusChip(d.status)}
            <span class="chip">Opened ${plural(d.age_days, "day")} ago</span>
            ${d.ring ? `<a class="chip share" href="#/rings/${esc(d.ring)}">Part of ${esc(d.ring)}</a>` : ""}</p>
        </div>
        <div class="big-score"><div class="n">${d.score}<small> / 100</small></div><div class="muted small">risk score, updated live</div></div>
      </div>
      <div class="compare">
        <div><div class="k">${d.own ? "Your bank's model alone" : "Its bank's model alone"}</div><div class="v num">${d.local_score}</div>
          <div class="muted small">alert threshold ${d.threshold_local}</div></div>
        <div><div class="k">With consortium intelligence</div><div class="v num">${d.score}</div>
          <div class="muted small">alert threshold ${d.threshold}</div></div>
        <div><div class="k">Where the evidence comes from</div>
          <div class="evidence-split" role="img" aria-label="Own-bank data ${pct(own / total)}, consortium ${pct(cons / total)}">
            <i class="own" style="width:${own / total * 100}%"></i><i class="cons" style="width:${cons / total * 100}%"></i></div>
          <div class="legend"><span><i class="sw sq" style="background:#8b95a3"></i>Own-bank data ${pct(own / total)}</span>
            <span><i class="sw sq" style="background:var(--accent)"></i>Consortium ${pct(cons / total)}</span></div></div>
      </div>`;
  }
  function reasonsHtml(d) {
    const maxImpact = Math.max(...d.reasons.raises.concat(d.reasons.lowers).map((r) => Math.abs(r.impact)), 0.01);
    const reason = (r, cls) => `
      <div class="reason"><div><div class="text">${esc(r.text)}</div>
        <div class="meta"><span class="chip">${esc(r.label)}</span>
          ${r.source === "consortium" ? `<span class="chip share">Consortium intelligence</span>` : `<span class="chip own">Own-bank data</span>`}</div></div>
        <div class="impact ${cls}" title="${cls === "raise" ? "Raises" : "Lowers"} the risk"><i style="width:${Math.abs(r.impact) / maxImpact * 100}%"></i></div></div>`;
    return `<h3>Raises the risk</h3>
      ${d.reasons.raises.map((r) => reason(r, "raise")).join("") || `<p class="muted">Nothing notable.</p>`}
      ${d.reasons.lowers.length ? `<h3>Lowers the risk</h3>${d.reasons.lowers.map((r) => reason(r, "lower")).join("")}` : ""}`;
  }
  const flowsHtml = (d) => d.flows.map((f) => `<tr class="clickable" tabindex="0" data-token="${esc(f.party.token)}">
    <td data-label="Direction">${f.dir === "in" ? "Received from" : "Sent to"}</td>
    <td data-label="Account">${f.party.own ? esc(f.party.label) : `<span class="mono">${esc(f.party.label)}</span>`}</td>
    <td data-label="Bank">${bankChip(f.party.bank, f.party.bank_name)}</td><td data-label="Risk">${bandChip(f.party.band)}</td>
    <td data-label="Total" class="r num">${inr(f.amount)}</td><td data-label="Payments" class="r num">${f.count}</td></tr>`).join("")
    || `<tr><td colspan="6" class="muted lead">No payments yet.</td></tr>`;
  const txHtml = (d) => d.transactions.map((t) => `<tr><td data-label="Time" class="num" style="white-space:nowrap">${when(t.time)}</td>
    <td data-label="Direction">${t.dir === "in" ? "Credit" : "Debit"}</td><td data-label="Counterparty">${esc(t.party)}</td>
    <td data-label="Channel">${esc(t.channel)}</td>
    <td data-label="Amount" class="r num">${t.dir === "in" ? "+" : "−"}${inr(t.amount)}</td></tr>`).join("")
    || `<tr><td colspan="5" class="muted lead">No transactions in the last 30 days.</td></tr>`;

  // The case file: one per account, owned by the bank. Create, read, update and delete all happen here.
  const caseSig = (d) => JSON.stringify(d.case ? [d.case.id, d.case.version, d.case.state, d.case.priority, d.case.note_count,
    (d.case.notes || []).map((n) => [n.id, n.updated_at])] : null);
  function caseHtml(d) {
    const c = d.case;
    if (!d.can_act) {
      return c
        ? `<p class="small" style="margin-top:12px"><b>Case #${c.id}</b> at ${esc(c.bank_name)}: ${caseStateChip(c.state)} ${prioChip(c.priority)}
            <span class="chip">${plural(c.note_count, "note")}</span><br><span class="muted">Its summary, assignee and notes stay with the bank.</span></p>`
        : `<p class="muted small" style="margin-top:12px">The bank has not opened a case on this account.</p>`;
    }
    if (!c) {
      return `<h3>Case file</h3>
        <p class="muted small">No case yet. Recording a decision opens one automatically, or open one now to start taking notes.</p>
        <button class="btn" data-case="create">Open a case</button>`;
    }
    const opt = (v, label) => `<option value="${v}" ${c.priority === v ? "selected" : ""}>${label}</option>`;
    return `<h3>Case file</h3>
      <p class="small" style="margin:0 0 10px"><b>Case #${c.id}</b> ${caseStateChip(c.state)}
        <span class="muted">opened by ${esc(c.created_by)} on ${when(c.created_at)}</span></p>
      <form id="case-form" class="form-grid" autocomplete="off">
        <label class="wide"><span>Summary</span><input name="title" maxlength="120" value="${esc(c.title)}" required></label>
        <label><span>Priority</span><select name="priority">${opt("high", "High")}${opt("medium", "Medium")}${opt("low", "Low")}</select></label>
        <label><span>Assigned to</span><input name="assignee" maxlength="60" value="${esc(c.assignee)}" placeholder="Reviewer's name"></label>
        <div class="actions wide">
          <button class="btn btn-primary" type="submit">Save changes</button>
          <button class="btn" type="button" data-case="toggle">${c.state === "open" ? "Close case" : "Reopen case"}</button>
          <button class="btn btn-quiet-danger" type="button" data-case="delete">Delete case</button>
        </div>
      </form>
      <h3>Notes (${c.notes.length})</h3>
      ${c.notes.length ? `<ul class="notes">${c.notes.map((n) => `
        <li data-note="${n.id}">
          <div class="note-meta"><b>${esc(n.author)}</b> <span class="muted">${when(n.created_at)}${n.edited ? " · edited" : ""}</span>
            <span class="note-actions"><button class="link" data-note-act="edit">Edit</button><button class="link danger" data-note-act="delete">Delete</button></span></div>
          <div class="note-body">${esc(n.body)}</div>
        </li>`).join("")}</ul>` : `<p class="muted small">No notes yet.</p>`}
      <form id="note-form" autocomplete="off">
        <label class="small muted" for="new-note">Add a note (stays inside your bank)</label>
        <textarea id="new-note" maxlength="2000" placeholder="For example: matched to a complaint on the cybercrime helpline" required></textarea>
        <div class="actions"><button class="btn" type="submit">Add note</button></div>
      </form>`;
  }
  const historyHtml = (d) => d.history.length ? `<h3>Case history</h3><table><tbody>${d.history.map((h) => `
    <tr><td class="muted small" style="white-space:nowrap">${when(h.ts)}</td><td class="small"><b>${esc(h.actor)}</b><br>${esc(h.detail)}
      ${h.note ? `<br><span class="muted">Note: ${esc(h.note)}</span>` : h.note_hidden ? `<br><span class="muted">A note was recorded. It is visible to the bank only.</span>` : ""}</td></tr>`).join("")}</tbody></table>` : "";

  async function accountDetail(token, id) {
    let d = await api(`/api/accounts/${encodeURIComponent(token)}${truthQs()}`);
    if (stale(id)) return;
    if (d.restricted) {
      app.innerHTML = `<a class="back" href="#/alerts">← Back to alerts</a>
        <div class="card"><h1 class="mono">${esc(d.token)}</h1>
        <p style="margin:8px 0">${bankChip(d.bank, d.bank_name)} ${bandChip(d.band)} ${statusChip(d.status)}
          ${d.ring ? `<a class="mono" href="#/rings/${esc(d.ring)}">${esc(d.ring)}</a>` : ""}</p>
        <div class="notice">${esc(d.message)}</div></div>`;
      return;
    }
    app.innerHTML = `
      <a class="back" href="#/alerts">← Back to alerts</a>
      <div class="card" id="d-head">${headHtml(d)}</div>
      <div class="section grid cols-detail">
        <div class="stack">
          <div class="card">
            <h2>Why this account was flagged</h2>
            <p class="sub">Each line is a fact about the account. The bar shows how strongly it moved the score.</p>
            <div id="d-reasons">${reasonsHtml(d)}</div>
          </div>
          <div class="card" id="decision">
            <h2>Decision and case</h2>
            ${d.can_act ? `
              <p class="sub">The recommended first step is a temporary hold while you review. Nothing is blocked automatically.</p>
              <div class="actions">
                <button class="btn btn-primary" data-act="hold">Hold and review</button>
                <button class="btn btn-danger" data-act="confirm">Confirm as mule</button>
                <button class="btn" data-act="clear">Clear as legitimate</button>
                <button class="btn" data-act="reopen">Reopen</button>
              </div>` : `<div class="notice">The consortium hub is read-only. Only the bank that holds an account can place a hold or record a decision.</div>`}
            <div id="ripple" style="margin-top:12px"></div>
            <div id="case-body">${caseHtml(d)}</div>
            <div id="d-history">${historyHtml(d)}</div>
          </div>
        </div>
        <div class="stack">
          <div class="card">
            <h2>Money trail</h2>
            <p class="sub">Who paid this account and where the money went. Click an account to open it.</p>
            <div class="graph" id="graph"></div>
            ${graphLegend()}
          </div>
          <div class="card">
            <h2>Largest counterparties</h2>
            <div class="table-wrap"><table class="stack">
              <thead><tr><th>Direction</th><th>Account</th><th>Bank</th><th>Risk</th><th class="r">Total</th><th class="r">Payments</th></tr></thead>
              <tbody id="d-flows">${flowsHtml(d)}</tbody>
            </table></div>
          </div>
        </div>
      </div>
      ${d.transactions ? `<div class="section card">
        <h2>Recent transactions</h2>
        <p class="sub">From your bank's own ledger, newest first. Individual transactions never leave the bank.</p>
        <div class="table-wrap"><table class="stack">
          <thead><tr><th>Time</th><th>Direction</th><th>Counterparty</th><th>Channel</th><th class="r">Amount</th></tr></thead>
          <tbody id="d-tx">${txHtml(d)}</tbody>
        </table></div></div>` : ""}`;

    const paintGraph = (g) => { const sig = graphSig(g); if (sig !== S.graphSig) { S.graphSig = sig; drawGraph($("#graph"), g, "cose"); } };
    const setButtons = (disabled) => app.querySelectorAll("[data-act]").forEach((b) => {
      b.disabled = disabled || (b.dataset.act === "reopen" && (d.status === "open" || d.status === "none"));
    });
    paintGraph(d.graph); bindRows(); setButtons(false);

    // Live: when the server has rescored, refresh the parts that changed and leave the rest alone.
    let sig = detailSig(d), csig = caseSig(d), busy = false, again = false;
    // Someone typing in the case card must not have it redrawn under them.
    const editingCase = () => {
      const box = $("#case-body"), a = document.activeElement;
      if (!box) return false;
      return (a && box.contains(a) && /INPUT|TEXTAREA|SELECT/.test(a.tagName)) || !!$(".note-edit", box)
        || !!($("#new-note") && $("#new-note").value.trim());
    };
    const refresh = async (force) => {
      if (stale(id)) return;
      if (busy) { again = again || !!force; return; }
      busy = true;
      try {
        const fresh = await api(`/api/accounts/${encodeURIComponent(token)}${truthQs()}`);
        if (stale(id) || fresh.restricted) return;
        const s2 = detailSig(fresh), c2 = caseSig(fresh);
        d = fresh;
        if (s2 !== sig) {
          sig = s2;
          $("#d-head").innerHTML = headHtml(d);
          $("#d-reasons").innerHTML = reasonsHtml(d);
          $("#d-flows").innerHTML = flowsHtml(d);
          if ($("#d-tx")) $("#d-tx").innerHTML = txHtml(d);
          $("#d-history").innerHTML = historyHtml(d);
          bindRows(); setButtons(false);
        }
        if (c2 !== csig && (force || !editingCase())) { csig = c2; $("#case-body").innerHTML = caseHtml(d); bindCase(); }
        paintGraph(fresh.graph);
      } catch (_) { /* the next tick tries again */ } finally {
        busy = false;
        if (again) { again = false; refresh(true); }
      }
    };
    S.hooks.tick = (p) => { if (p.version !== d.version) refresh(); };
    S.hooks.event = (p) => { if (p.type === "case") refresh(); };

    // One place for every change to the case: run it, report a failure in plain words, then redraw.
    const change = async (what, fn) => {
      try { await fn(); } catch (err) { toast(`<b>${esc(what)}</b>${esc(err.message)}`); }
      await refresh(true);
      refreshCaseCount();
    };
    function bindCase() {
      const box = $("#case-body"), c = d.case;
      if (!box || !d.can_act) return;
      const create = $('[data-case="create"]', box);
      if (create) create.onclick = () => change("Could not open the case", () => send("POST", "/api/cases", { token }));
      if (!c) return;
      const url = `/api/cases/${c.id}`;
      $("#case-form").onsubmit = (ev) => {
        ev.preventDefault();
        const f = new FormData(ev.target);
        change("Could not save the case", async () => {
          await send("PATCH", url, { version: c.version, title: f.get("title"), priority: f.get("priority"), assignee: f.get("assignee") });
          toast("<b>Case saved</b>Your changes are stored.", 3000);
        });
      };
      $('[data-case="toggle"]', box).onclick = () => change("Could not update the case",
        () => send("PATCH", url, { version: c.version, state: c.state === "open" ? "closed" : "open" }));
      $('[data-case="delete"]', box).onclick = () => {
        if (!confirm(`Delete case #${c.id} and its ${c.notes.length} note(s)? The decision on this account is reset to Open. This cannot be undone.`)) return;
        change("Could not delete the case", () => send("DELETE", url));
      };
      $("#note-form").onsubmit = (ev) => {
        ev.preventDefault();
        const body = $("#new-note").value.trim();
        if (!body) return;
        $("#new-note").value = "";
        change("Could not add the note", () => send("POST", `${url}/notes`, { body }));
      };
      box.querySelectorAll("li[data-note]").forEach((li) => {
        const note = c.notes.find((n) => String(n.id) === li.dataset.note);
        $('[data-note-act="delete"]', li).onclick = () => {
          if (!confirm("Delete this note? This cannot be undone.")) return;
          change("Could not delete the note", () => send("DELETE", `${url}/notes/${note.id}`));
        };
        $('[data-note-act="edit"]', li).onclick = () => {
          $(".note-body", li).innerHTML = `<form class="note-edit"><label class="sr-only" for="edit-${note.id}">Edit note</label>
            <textarea id="edit-${note.id}" maxlength="2000" required>${esc(note.body)}</textarea>
            <div class="actions"><button class="btn btn-primary" type="submit">Save note</button><button class="btn" type="button" data-cancel>Cancel</button></div></form>`;
          const form = $(".note-edit", li), area = $("textarea", form);
          area.focus(); area.setSelectionRange(area.value.length, area.value.length);
          $("[data-cancel]", form).onclick = () => { $("#case-body").innerHTML = caseHtml(d); bindCase(); };
          form.onsubmit = (ev) => {
            ev.preventDefault();
            const body = area.value.trim();
            if (!body) return;
            form.remove();
            change("Could not save the note", () => send("PATCH", `${url}/notes/${note.id}`, { body }));
          };
        };
      });
    }
    bindCase();

    app.querySelectorAll("[data-act]").forEach((btn) => {
      btn.onclick = async () => {
        setButtons(true);
        try {
          const out = await send("POST", `/api/accounts/${encodeURIComponent(token)}/action`, { action: btn.dataset.act });
          $("#ripple").innerHTML = rippleHtml(out);
          bindRows($("#ripple"));
          await refresh(true);
          refreshCaseCount();
        } catch (err) {
          toast(`<b>Could not record the decision</b>${esc(err.message)}`);
        }
        setButtons(false);
      };
    });
  }

  // ------------------------------------------------------------ cases
  async function cases(_, id) {
    const hub = isHub();
    app.innerHTML = `
      <h1>Cases</h1>
      <p class="sub">${hub
        ? "Case files belong to the banks. The hub can see that a case exists, its state and its priority, but not its summary, assignee or notes."
        : "One case file per account under review: who is handling it, how urgent it is, and the notes taken along the way."}</p>
      <div class="toolbar">
        <span id="case-filters"></span>
        ${hub ? "" : `<button class="btn btn-primary" id="new-case" style="margin-left:auto">New case</button>`}
      </div>
      <div id="case-new"></div>
      <div class="card" style="padding:6px 8px"><div class="table-wrap"><table class="stack">
        <thead><tr><th>Case</th><th>Account</th>${hub ? "<th>Bank</th>" : "<th>Assigned to</th>"}<th>Priority</th><th>Decision</th>
          <th>State</th><th class="r">Notes</th><th>Updated</th>${hub ? "" : "<th></th>"}</tr></thead>
        <tbody id="case-rows"><tr><td colspan="9" class="muted lead" style="padding:24px">Loading…</td></tr></tbody>
      </table></div></div>`;
    let list = [];
    const paint = () => {
      const count = (k) => list.filter((c) => k === "all" || c.state === k).length;
      $("#case-filters").innerHTML = [["open", "Open"], ["closed", "Closed"], ["all", "All"]].map(([k, label]) =>
        `<button class="filter" data-filter="${k}" aria-pressed="${S.caseFilter === k}">${label} <span class="muted">${count(k)}</span></button>`).join(" ");
      app.querySelectorAll("#case-filters .filter").forEach((b) => { b.onclick = () => { S.caseFilter = b.dataset.filter; paint(); }; });
      const rows = list.filter((c) => S.caseFilter === "all" || c.state === S.caseFilter);
      $("#case-rows").innerHTML = rows.map((c) => `
        <tr data-case-id="${c.id}">
          <td class="lead"><a href="#/alerts/${esc(c.token)}"><b>#${c.id}</b> ${hub ? "" : esc(c.title)}</a></td>
          <td data-label="Account">${c.account ? (c.account.own ? esc(c.account.label) : `<span class="mono">${esc(c.account.label)}</span>`) : `<span class="muted">no longer in the data</span>`}</td>
          ${hub ? `<td data-label="Bank">${bankChip(c.bank, c.bank_name)}</td>` : `<td data-label="Assigned to">${c.assignee ? esc(c.assignee) : `<span class="muted">nobody yet</span>`}</td>`}
          <td data-label="Priority">${prioChip(c.priority)}</td>
          <td data-label="Decision">${statusChip(c.decision)}</td>
          <td data-label="State">${caseStateChip(c.state)}</td>
          <td data-label="Notes" class="r num">${c.note_count}</td>
          <td data-label="Updated" class="num" style="white-space:nowrap">${when(c.updated_at)}</td>
          ${hub ? "" : `<td class="row-actions"><button class="btn btn-small" data-row="toggle">${c.state === "open" ? "Close" : "Reopen"}</button>
            <button class="btn btn-small btn-quiet-danger" data-row="delete">Delete</button></td>`}
        </tr>`).join("") || `<tr><td colspan="9" class="muted lead" style="padding:24px">${list.length ? "No cases match this filter." : hub ? "No bank has opened a case yet." : "No cases yet. Open one from an alert, or use New case."}</td></tr>`;
      app.querySelectorAll("#case-rows tr[data-case-id]").forEach((tr) => {
        const c = list.find((x) => String(x.id) === tr.dataset.caseId);
        const run = async (what, fn) => { try { await fn(); } catch (err) { toast(`<b>${esc(what)}</b>${esc(err.message)}`); } await load(); };
        const toggle = $('[data-row="toggle"]', tr), del = $('[data-row="delete"]', tr);
        if (toggle) toggle.onclick = () => run("Could not update the case",
          () => send("PATCH", `/api/cases/${c.id}`, { version: c.version, state: c.state === "open" ? "closed" : "open" }));
        if (del) del.onclick = () => {
          if (!confirm(`Delete case #${c.id} and its ${c.note_count} note(s)? The decision on the account is reset to Open. This cannot be undone.`)) return;
          run("Could not delete the case", () => send("DELETE", `/api/cases/${c.id}`));
        };
      });
    };
    const load = async () => {
      const fresh = await api("/api/cases");
      if (stale(id)) return;
      list = fresh; paint(); paintCaseCount(list);
    };
    if (!hub) {
      $("#new-case").onclick = async () => {
        const box = $("#case-new");
        if (box.innerHTML) { box.innerHTML = ""; return; }
        let open = [];
        try { open = (await api("/api/alerts")).filter((a) => !a.case); } catch (err) { toast(`<b>Could not load alerts</b>${esc(err.message)}`); return; }
        if (stale(id)) return;
        box.innerHTML = `<form class="card form-grid" id="case-create" autocomplete="off" style="margin-bottom:14px">
          <label class="wide"><span>Account</span><select name="token" required>
            ${open.length ? open.map((a) => `<option value="${esc(a.token)}">${esc(a.label)} · risk ${a.score}</option>`).join("")
              : `<option value="">Every flagged account already has a case</option>`}</select></label>
          <label class="wide"><span>Summary</span><input name="title" maxlength="120" placeholder="What is being checked"></label>
          <label><span>Priority</span><select name="priority"><option value="high">High</option><option value="medium" selected>Medium</option><option value="low">Low</option></select></label>
          <label><span>Assigned to</span><input name="assignee" maxlength="60" placeholder="Reviewer's name"></label>
          <div class="actions wide"><button class="btn btn-primary" type="submit" ${open.length ? "" : "disabled"}>Create case</button>
            <button class="btn" type="button" data-cancel>Cancel</button></div></form>`;
        $("[data-cancel]", box).onclick = () => { box.innerHTML = ""; };
        $("#case-create").onsubmit = async (ev) => {
          ev.preventDefault();
          const f = new FormData(ev.target);
          try {
            const c = await send("POST", "/api/cases", { token: f.get("token"), title: f.get("title"), priority: f.get("priority"), assignee: f.get("assignee") });
            box.innerHTML = "";
            toast(`<b>Case #${c.id} created</b><a href="#/alerts/${esc(c.token)}">Open it to add notes</a>`, 5000);
            S.caseFilter = "open";
          } catch (err) { toast(`<b>Could not create the case</b>${esc(err.message)}`); }
          await load();
        };
        $("select", box).focus();
      };
    }
    await load();
    const reload = debounce(() => load().catch(() => {}), 400);
    S.hooks.event = (p) => { if (p.type === "case") reload(); };
  }
  function rippleHtml(out) {
    const head = `<div class="notice ok"><b>Decision recorded.</b> `;
    if (!out.ripple.length && !out.newly_flagged && !out.no_longer_flagged) {
      return head + `It has been shared with the consortium. No linked account changed risk band.</div>`;
    }
    return head + `It was shared with the consortium and linked accounts were rescored:
      ${plural(out.newly_flagged, "account")} newly flagged, ${out.no_longer_flagged} no longer flagged.
      <table style="margin-top:6px"><tbody>${out.ripple.map((c) => `<tr class="clickable" tabindex="0" data-token="${esc(c.token)}">
        <td>${c.own ? esc(c.label) : `<span class="mono">${esc(c.label)}</span>`}</td><td>${bankChip(c.bank, c.bank_name)}</td>
        <td>${bandChip(c.before_band)} → ${bandChip(c.band)}</td></tr>`).join("")}</tbody></table></div>`;
  }

  // ------------------------------------------------------------ graph
  const graphLegend = () => `<div class="legend" style="margin-top:10px">
      ${Object.entries(BANK_COLOR).map(([c, col]) => `<span><i class="sw" style="background:${col}"></i>Bank ${c}</span>`).join("")}
      <span><i class="sw ring"></i>Flagged high-risk</span>
      <span><i class="sw sq" style="background:#8b95a3"></i>Cash withdrawal</span>
      <span class="muted">Arrow width grows with the amount moved</span></div>`;

  /* Ring accounts on an inner circle, cash in the middle, outside accounts on an
     outer circle, each placed near the ring account it deals with. */
  function ringPositions(g) {
    const members = g.nodes.filter((n) => n.member);
    const order = { Collector: 0, Mover: 1, "Exit point": 2 };
    members.sort((a, b) => (order[a.role] ?? 3) - (order[b.role] ?? 3) || a.id.localeCompare(b.id));
    const r1 = Math.max(110, members.length * 20);
    const pos = {}, angle = {};
    members.forEach((n, i) => {
      angle[n.id] = (2 * Math.PI * i) / members.length - Math.PI / 2;
      pos[n.id] = { x: r1 * Math.cos(angle[n.id]), y: r1 * Math.sin(angle[n.id]) };
    });
    const near = {};
    g.edges.forEach((e) => {
      if (angle[e.t] !== undefined && near[e.s] === undefined) near[e.s] = angle[e.t];
      if (angle[e.s] !== undefined && near[e.t] === undefined) near[e.t] = angle[e.s];
    });
    const outside = g.nodes.filter((n) => !n.member && !n.atm).sort((a, b) => (near[a.id] ?? 9) - (near[b.id] ?? 9) || a.id.localeCompare(b.id));
    const r2 = Math.max(r1 + 150, (outside.length * 30) / (2 * Math.PI));
    outside.forEach((n, i) => {
      const a = (2 * Math.PI * i) / outside.length - Math.PI / 2;
      pos[n.id] = { x: r2 * Math.cos(a), y: r2 * Math.sin(a) };
    });
    pos.ATM = { x: 0, y: 0 };
    return pos;
  }

  function drawGraph(el, g, layout) {
    if (!el) return;
    if (S.cy) { S.cy.destroy(); S.cy = null; }
    if (!window.cytoscape) { el.innerHTML = `<p class="muted" style="padding:16px">Graph library failed to load.</p>`; return; }
    const maxAmt = Math.max(...g.edges.map((e) => e.amount), 1);
    const elements = g.nodes.map((n) => ({ data: {
      ...n, color: n.atm ? "#8b95a3" : (BANK_COLOR[n.bank] || "#999"),
      size: n.focus ? 34 : n.atm ? 26 : (n.member || n.flagged) ? 26 : 14,
      text: (n.focus || n.member || n.flagged || n.atm || n.own) ? n.label : "",
    }, classes: [n.flagged ? "flagged" : "", n.focus ? "focus" : "", n.atm ? "atm" : "",
      (!n.flagged && !n.focus && !n.atm) ? "outsider" : ""].join(" ") }))
      .concat(g.edges.map((e, i) => ({ data: {
        id: "e" + i, source: e.s, target: e.t, amount: e.amount, count: e.count,
        w: 1 + 5 * Math.sqrt(e.amount / maxAmt) } })));
    el.innerHTML = "";
    const cy = cytoscape({
      container: el, elements, minZoom: 0.3, maxZoom: 3,
      userZoomingEnabled: false,  // the mouse wheel scrolls the page; zoom with the buttons
      style: [
        { selector: "node", style: { "background-color": "data(color)", width: "data(size)", height: "data(size)",
          label: "data(text)", "font-size": 10, color: "#0f1720", "text-valign": "bottom", "text-margin-y": 4,
          "text-background-color": "#fbfcfd", "text-background-opacity": 0.85, "text-background-padding": 2,
          "border-width": 2, "border-color": "#fbfcfd" } },
        { selector: "node.outsider", style: { opacity: 0.55 } },
        { selector: "node.flagged", style: { "border-width": 4, "border-color": "#d03b3b" } },
        { selector: "node.focus", style: { "border-width": 5, "border-color": "#0f1720", "font-weight": 700, "font-size": 11 } },
        { selector: "node.focus.flagged", style: { "border-color": "#d03b3b" } },
        { selector: "node.atm", style: { shape: "round-rectangle" } },
        { selector: "edge", style: { width: "data(w)", "line-color": "#aab3bf", "target-arrow-color": "#aab3bf",
          "target-arrow-shape": "triangle", "curve-style": "bezier", "arrow-scale": 0.9, opacity: 0.8 } },
        { selector: "edge.hot", style: { "line-color": "#d03b3b", "target-arrow-color": "#d03b3b", opacity: 0.9 } },
      ],
      layout: layout === "ring"
        ? { name: "preset", positions: ringPositions(g), padding: 24, fit: true }
        : { name: "cose", animate: false, padding: 24, nodeRepulsion: 9000, idealEdgeLength: 70, randomize: false },
    });
    cy.edges().forEach((e) => { if (e.source().hasClass("flagged") && (e.target().hasClass("flagged") || e.target().hasClass("atm"))) e.addClass("hot"); });
    const tip = $("#tooltip");
    const show = (html, ev) => {
      tip.innerHTML = html; tip.hidden = false;
      const p = ev.originalEvent;
      tip.style.left = Math.min(p.clientX + 14, window.innerWidth - 300) + "px";
      tip.style.top = (p.clientY + 14) + "px";
    };
    cy.on("mouseover mousemove", "node", (ev) => {
      const n = ev.target.data();
      show(n.atm ? `<b>Cash withdrawn at ATMs</b>` : `<b>${esc(n.label)}</b>${n.bank ? "Bank " + esc(n.bank) : ""} · ${bandWord[n.band]}
        ${n.role ? `<br>Role in ring: ${esc(n.role)}` : ""}${n.own ? "<br>Your customer" : ""}
        ${n.truth ? `<br>Label: ${n.truth.is_mule ? "mule" : "legitimate"}` : ""}`, ev);
    });
    cy.on("mouseover mousemove", "edge", (ev) => {
      const e = ev.target.data();
      show(`<b>${inr(e.amount)}</b>in ${plural(e.count, "payment")}`, ev);
    });
    cy.on("mouseout", () => { tip.hidden = true; });
    cy.on("tap", "node", (ev) => { if (!ev.target.data("atm")) location.hash = `#/alerts/${ev.target.id()}`; });
    const zoom = document.createElement("div");
    zoom.className = "graph-zoom";
    zoom.innerHTML = `<button aria-label="Zoom in">+</button><button aria-label="Zoom out">−</button><button aria-label="Fit graph to view">Fit</button>`;
    const [zin, zout, fit] = zoom.children;
    const centre = () => ({ x: el.clientWidth / 2, y: el.clientHeight / 2 });
    zin.onclick = () => cy.zoom({ level: cy.zoom() * 1.3, renderedPosition: centre() });
    zout.onclick = () => cy.zoom({ level: cy.zoom() / 1.3, renderedPosition: centre() });
    fit.onclick = () => cy.fit(undefined, 24);
    el.appendChild(zoom);
    S.cy = cy;
  }

  // ------------------------------------------------------------ rings
  async function rings(ringId, id) {
    if (ringId) return ringDetail(ringId, id);
    const hub = isHub();
    app.innerHTML = `
      <h1>Suspected mule rings</h1>
      <p class="sub">Flagged accounts that pass money to each other or share a device are grouped into one ring.
        ${hub ? "No single bank can see a whole ring; the hub joins the pieces using tokens." : "Members at other banks appear as tokens."}
        Rings appear and grow here as they are detected.</p>
      <div class="ring-grid" id="ring-grid"><p class="muted">Loading…</p></div>`;
    const load = async () => {
      const list = await api("/api/rings");
      if (stale(id)) return;
      $("#ring-grid").innerHTML = list.map((r) => `
        <a class="ring-card" href="#/rings/${esc(r.id)}">
          <div class="top"><b class="mono">${esc(r.id)}</b>${r.is_new ? `<span class="chip new">New</span>` : `<span class="chip high">Avg risk ${r.mean_score}</span>`}</div>
          <div class="amount">${inrShort(r.funnelled)}</div>
          <div class="muted small">collected from ${plural(r.senders, "outside account")}</div>
          <div class="facts"><span><b>${r.size}</b> accounts</span><span><b>${r.banks.length}</b> banks</span>
            ${hub ? "" : `<span><b>${r.own_members}</b> at your bank</span>`}<span>${inrShort(r.cash_withdrawn)} taken as cash</span></div>
          <div class="chips">${r.banks.map((b) => bankChip(b.slice(-1), b)).join("")}</div>
          <div class="muted small" style="margin-top:8px">Active ${when(r.first)} to ${when(r.last)}</div>
        </a>`).join("") || `<p class="muted">No rings involve your accounts right now.</p>`;
    };
    await load();
    const reload = debounce(() => load().catch(() => {}), 500);
    S.hooks.event = (p) => { if (["ring", "alert", "case"].includes(p.type)) reload(); };
  }

  const ringSig = (r) => JSON.stringify([r.size, r.funnelled, r.cash_withdrawn, r.sent_onward, r.senders_total,
    r.members.map((m) => [m.token, m.band, m.status, m.role])]);

  async function ringDetail(ringId, id) {
    let r = await api(`/api/rings/${encodeURIComponent(ringId)}${truthQs()}`);
    if (stale(id)) return;
    const hub = isHub();
    const paint = () => {
      app.innerHTML = `
        <a class="back" href="#/rings">← Back to rings</a>
        <h1><span class="mono">${esc(r.id)}</span> ${r.is_new ? `<span class="chip new">New</span>` : ""}</h1>
        <p class="sub">${r.size} linked accounts across ${r.banks.length} banks, active from ${when(r.first)} to ${when(r.last)}. This page follows the ring as it changes.</p>
        <div class="kpis">
          ${kpi("r1", "Collected from outside", inrShort(r.funnelled), `from ${plural(r.senders_total, "account")}, possible victims`)}
          ${kpi("r2", "Withdrawn as cash", inrShort(r.cash_withdrawn), "at ATMs by ring accounts")}
          ${kpi("r3", "Sent on to other accounts", inrShort(r.sent_onward), "outside the ring")}
          ${kpi("r4", "Accounts in the ring", r.size, r.banks.join(", "))}
          ${hub ? "" : kpi("r5", "Your customers who paid in", r.own_senders, "people to contact and warn", true)}
        </div>
        <div class="section grid cols-ring">
          <div class="card">
            <h2>How the money moved</h2>
            <p class="sub">Outer circle: accounts that paid in. Inner circle: ring accounts. Showing the ${r.senders_shown} largest of ${r.senders_total} paying accounts.</p>
            <div class="graph tall" id="graph"></div>
            ${graphLegend()}
          </div>
          <div class="stack">
            <div class="card">
              <h2>Ring accounts</h2>
              <div class="table-wrap"><table class="stack">
                <thead><tr><th>Risk</th><th>Account</th><th>Bank</th><th>Role</th></tr></thead>
                <tbody>${r.members.map((m) => `<tr class="clickable" tabindex="0" data-token="${esc(m.token)}">
                  <td class="lead">${scoreBox(m)}</td><td class="lead">${m.own ? `<b>${esc(m.label)}</b>` : `<span class="mono">${esc(m.label)}</span>`} ${truthChip(m.truth)}
                    ${m.status !== "open" ? statusChip(m.status) : ""}</td>
                  <td data-label="Bank">${bankChip(m.bank, m.bank_name)}</td><td data-label="Role">${esc(m.role)}</td></tr>`).join("")}</tbody>
              </table></div>
              <p class="muted small" style="margin-top:10px">Collector: takes money from outside. Mover: passes it between ring accounts. Exit point: where it leaves, usually as cash.</p>
            </div>
            ${!hub && r.own_sender_names.length ? `<div class="card">
              <h2>Your customers who paid into this ring</h2>
              <p class="sub">They may be victims. Contacting them early helps stop further payments.</p>
              <div class="pill-list">${r.own_sender_names.map((n) => `<span class="chip">${esc(n)}</span>`).join("")}
                ${r.own_senders > r.own_sender_names.length ? `<span class="chip">and ${r.own_senders - r.own_sender_names.length} more</span>` : ""}</div>
            </div>` : ""}
          </div>
        </div>`;
      S.graphSig = "";
      drawGraph($("#graph"), r.graph, "ring");
      bindRows();
    };
    paint();
    let sig = ringSig(r), busy = false;
    S.hooks.tick = async (p) => {
      if (busy || p.version === r.version || stale(id)) return;
      busy = true;
      try {
        const fresh = await api(`/api/rings/${encodeURIComponent(ringId)}${truthQs()}`);
        if (stale(id)) return;
        const s2 = ringSig(fresh);
        r = fresh;
        if (s2 !== sig) { sig = s2; const y = window.scrollY; paint(); window.scrollTo(0, y); }
      } catch (_) { /* the ring may have dissolved; keep what is shown */ } finally { busy = false; }
    };
  }

  // ------------------------------------------------------------ privacy
  async function privacy(_, id) {
    const p = await api("/api/privacy");
    if (stale(id)) return;
    const kv = (rows) => `<table class="kv"><tbody>${rows.map(([k, v]) => `<tr><td>${esc(k)}</td><td class="mono">${esc(v)}</td></tr>`).join("")}</tbody></table>`;
    app.innerHTML = `
      <h1>Privacy and security</h1>
      <p class="sub">Banks cooperate on tokens and numbers, not on customer records. Below is one real record from the demo data, shown both ways.</p>
      ${p.default_key ? `<div class="notice warn" style="margin-bottom:16px"><b>Demo key in use.</b> Set the CONSORTIUM_HMAC_KEY environment variable to a long random secret before any real use.</div>` : ""}
      <div class="grid cols-2">
        <div class="card">
          <div class="side-title"><h2>Stays inside the bank</h2><span class="chip">never transmitted</span></div>
          ${p.raw ? kv(p.raw) : `<div class="notice">Not available to the consortium hub. Sign in as a bank analyst to see the bank's side of this record.</div>`}
          <h3>Never shared, by design</h3>
          <div class="pill-list">${p.never_shared.map((x) => `<span class="chip">${esc(x)}</span>`).join("")}</div>
        </div>
        <div class="card">
          <div class="side-title"><h2>Leaves the bank</h2><span class="chip share">share package</span></div>
          ${kv(p.shared)}
          <p class="muted small" style="margin-top:10px">"behaviour" holds ${plural(18, "numeric summary", "numeric summaries")} of the account's activity, such as how many accounts paid it in a day. "flows" are totals per counterparty token, not individual transactions.</p>
        </div>
      </div>

      <div class="section card">
        <h2>How sharing works</h2>
        <ol class="steps">
          <li><b>Tokenise.</b> Each bank turns account and device identifiers into tokens with HMAC-SHA256 and a secret shared only among member banks.</li>
          <li><b>Summarise.</b> The bank computes behaviour summaries and its own risk score for each account, inside its own systems.</li>
          <li><b>Share.</b> Tokens, summaries, scores and per-counterparty totals go to the hub. The hub does not hold the secret.</li>
          <li><b>Join.</b> The same account gets the same token at every bank, so the hub can link flows across banks and score the network.</li>
          <li><b>Return.</b> Each bank gets back scores and reasons for its own accounts. Other banks' accounts stay as tokens with a risk band.</li>
        </ol>
      </div>

      <div class="section card">
        <h2>How this system is protected</h2>
        <div class="table-wrap"><table>
          <thead><tr><th>Risk</th><th>What is in place</th></tr></thead>
          <tbody>
            <tr><td>Someone views another bank's customers</td><td>Your role comes from a signed session cookie, never from the page. Every request is checked on the server, and other banks' accounts are returned as a token and a risk band only.</td></tr>
            <tr><td>A stolen or forged session</td><td>Sessions are signed with HMAC-SHA256, expire after ${esc(p.session_hours)} hours, and the cookie cannot be read by scripts or sent from other sites.</td></tr>
            <tr><td>A malicious site acts in your name</td><td>Every change needs a header that other sites cannot send, the request origin is checked, and no cross-origin access is allowed.</td></tr>
            <tr><td>Injected scripts</td><td>A strict content security policy allows scripts from this site only. All text is escaped before it is shown.</td></tr>
            <tr><td>Password guessing and flooding</td><td>Requests are rate-limited per client, with tighter limits on sign-in and on changes. Oversized requests are refused.</td></tr>
            <tr><td>An automatic wrongful freeze</td><td>The system never blocks an account. It recommends a temporary hold, and a named analyst decides.</td></tr>
            <tr><td>Someone edits the audit trail</td><td>Each entry is chained to the one before with SHA-256. Any edit or deletion breaks the chain and is reported on the Audit trail page.</td></tr>
            <tr><td>Analyst notes leaking to the hub</td><td>Notes stay with the bank that wrote them. The hub sees that a decision was made, not the note.</td></tr>
          </tbody>
        </table></div>
        <p class="muted small" style="margin-top:10px">Access code at sign-in: <b>${p.access_code ? "required" : "not set (open demo)"}</b>.</p>
      </div>

      <div class="section grid cols-2">
        <div class="card">
          <h2>Packages in the current run</h2>
          <div class="table-wrap"><table>
            <thead><tr><th>From</th><th class="r">Account tokens</th><th class="r">Flow totals</th><th class="r">Device links</th><th class="r">Identities</th></tr></thead>
            <tbody>${p.packages.map((x) => `<tr><td>${bankChip(x.bank, "Bank " + x.bank)}</td><td class="r num">${num(x.accounts)}</td>
              <td class="r num">${num(x.flows)}</td><td class="r num">${num(x.device_links)}</td><td class="r num"><b>0</b></td></tr>`).join("")}</tbody>
          </table></div>
          <p class="muted small" style="margin-top:10px">Fields in a package: <span class="mono">${p.shared_fields.map(esc).join(", ")}</span></p>
        </div>
        <div class="card">
          <h2>Honest limits</h2>
          <ul class="small" style="margin:0;padding-left:18px;color:var(--ink-2)">
            <li>Tokens are pseudonymous, not anonymous. The hub can see which tokens pay which, so shared data still needs protection as personal data under the DPDP Act, 2023.</li>
            <li>Any party holding the secret could test guesses against a token. The secret belongs in a hardware security module at each bank, and the hub must never receive it.</li>
            <li>Sign-in here is a role choice with an optional access code. A production system would use each bank's single sign-on with multi-factor authentication.</li>
            <li>Stronger options for a production system: private set intersection, or running the hub inside a secure enclave.</li>
          </ul>
        </div>
      </div>`;
  }

  // ------------------------------------------------------------ audit
  async function audit(_, id) {
    const word = { hold: "Hold", confirm: "Confirm", clear: "Clear", reopen: "Reopen", rescore: "Rescore", startup: "Start-up",
      attack: "Simulated attack", reset: "Reset", stream: "Live stream", signin: "Sign-in", "signin-failed": "Failed sign-in",
      "case-create": "Case opened", "case-update": "Case updated", "case-delete": "Case deleted",
      "note-add": "Note added", "note-edit": "Note edited", "note-delete": "Note deleted" };
    app.innerHTML = `
      <h1>Audit trail</h1>
      <p class="sub">Every decision and system event is recorded with who did it and when. ${isHub()
        ? "The hub sees events from every bank, but not the analysts' notes." : "You see your own bank's entries and system events."}
        New entries appear by themselves.</p>
      <div id="chain"></div>
      <div class="card" style="padding:6px 8px"><div class="table-wrap"><table class="stack">
        <thead><tr><th>Event</th><th>Time</th><th>Who</th><th>Account token</th><th>Details</th><th>Entry hash</th></tr></thead>
        <tbody id="audit-rows"><tr><td colspan="6" class="muted" style="padding:24px">Loading…</td></tr></tbody>
      </table></div></div>`;
    const load = async () => {
      const a = await api("/api/audit");
      if (stale(id)) return;
      $("#chain").innerHTML = a.chain.ok
        ? `<div class="notice ok" style="margin-bottom:14px"><b>Audit chain verified.</b> All ${num(a.chain.entries)} entries are intact and in order.</div>`
        : `<div class="notice warn" style="margin-bottom:14px" role="alert"><b>Audit chain broken at entry ${esc(a.chain.broken_at)}.</b> An entry was changed or removed after it was written.</div>`;
      $("#audit-rows").innerHTML = a.entries.map((x) => `<tr><td class="lead"><span class="chip">${esc(word[x.action] || x.action)}</span>
          <span class="only-narrow muted small"> ${when(x.ts)}</span></td>
        <td data-label="Time" class="num hide-narrow" style="white-space:nowrap">${when(x.ts)}</td><td data-label="Who">${esc(x.actor)}</td>
        <td data-label="Account">${x.token ? `<a class="mono" href="#/alerts/${esc(x.token)}">${esc(x.token)}</a>` : `<span class="muted">none</span>`}</td>
        <td data-label="Details"><span>${esc(x.detail)}${x.note ? `<br><span class="muted">Note: ${esc(x.note)}</span>` : x.note_hidden ? `<br><span class="muted">Note recorded, visible to the bank only.</span>` : ""}</span></td>
        <td data-label="Entry hash" class="mono muted">${esc(x.hash)}</td></tr>`).join("");
    };
    await load();
    const reload = debounce(() => load().catch(() => {}), 500);
    S.hooks.event = (p) => { if (["case", "system"].includes(p.type)) reload(); };
  }

  // ------------------------------------------------------------ start-up
  async function loadActivity() {
    try {
      const a = await api("/api/activity");
      S.activity = a.events; S.clock = a.clock; S.running = a.running; S.speed = a.speed; S.version = a.version;
      S.attack = a.attack; S.ledger = [];
      paintAttack(); paintLive();
    } catch (_) { /* the stream will fill it in */ }
  }

  async function startApp(session) {
    S.session = session;
    S.seen.clear(); S.alertFilter = "all"; S.search = "";
    $("#controls").hidden = false; $("#tabs").hidden = false;
    document.querySelectorAll(".demo-only").forEach((el) => { el.hidden = !session.demo_controls; });
    const sel = $("#role");
    sel.innerHTML = session.roles.map((r) => `<option value="${esc(r.code)}">${esc(r.name)}</option>`).join("");
    sel.value = session.role;
    $("#truth").checked = S.truth;
    paintLive();
    await loadActivity();
    connect();
    if (!location.hash || location.hash === "#/") location.hash = "#/overview"; else render();
    try { paintCounts((await api("/api/overview")).kpis); } catch (_) { /* the first tick fills it in */ }
    paintCaseCount();
  }

  function wireControls() {
    const closeMenu = () => $("#menu").removeAttribute("open");
    $("#role").onchange = async (ev) => {
      try {
        const session = await send("POST", "/api/session", { role: ev.target.value });
        if (location.hash.split("/").length > 2) history.replaceState(null, "", "#/" + location.hash.split("/")[1]);
        await startApp(session);
      } catch (err) { ev.target.value = S.session.role; toast(`<b>Could not switch role</b>${esc(err.message)}`); }
    };
    $("#truth").onchange = (ev) => { S.truth = ev.target.checked; localStorage.setItem("mrs.truth", S.truth ? "1" : "0"); render(); };
    $("#live").onclick = async () => {
      if (!S.session.demo_controls || !S.connected) return;
      try { const out = await send("POST", "/api/stream", { running: !S.running }); S.running = out.running; paintLive(); }
      catch (err) { toast(`<b>Could not change the stream</b>${esc(err.message)}`); }
    };
    $("#attack").onclick = async (ev) => {
      const btn = ev.currentTarget;
      btn.disabled = true;
      try {
        const a = await send("POST", "/api/attack");
        S.attackDismissed = 0; S.attack = a; paintAttack();
        toast(`<b>Simulated attack started</b>${a.members} mule accounts across ${a.banks.length} banks will move money over the next minute or two. Watch the alerts arrive.`, 7000);
      } catch (err) { toast(`<b>Could not start an attack</b>${esc(err.message)}`); }
      btn.disabled = false;
    };
    $("#reset").onclick = async () => {
      closeMenu();
      if (!confirm("Restore the demo data to its starting state? Case decisions will be cleared for everyone.")) return;
      try { await send("POST", "/api/reset"); if (location.hash !== "#/overview") location.hash = "#/overview"; }
      catch (err) { toast(`<b>Could not reset</b>${esc(err.message)}`); }
    };
    $("#signout").onclick = async () => {
      closeMenu();
      try { showSignIn(await send("DELETE", "/api/session")); } catch (_) { location.reload(); }
    };
    document.addEventListener("click", (ev) => { if (!ev.target.closest("#menu")) closeMenu(); });
    document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") { closeMenu(); $("#toast").hidden = true; } });
    window.addEventListener("hashchange", () => { closeMenu(); render(); });
    $("#role").addEventListener("change", closeMenu);
  }

  async function boot() {
    wireControls();
    const info = await api("/api/session");
    if (info.role) await startApp(info); else showSignIn(info);
  }
  boot().catch((err) => { app.innerHTML = `<div class="card"><h2>Could not start</h2><p>${esc(err.message)}</p></div>`; });
})();
