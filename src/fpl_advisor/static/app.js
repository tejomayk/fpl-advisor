// FPL Advisor front end. No build step, no external requests.
// Every string from the API is untrusted: it only reaches the DOM through html`` (which escapes
// interpolations) or md() (escape first, then turn **pairs** into <strong>).

const $ = (sel, root = document) => root.querySelector(sel);

/* ---------------- Safe HTML ---------------- */
class Raw {
  constructor(s) { this.s = s; }
}
const raw = (s) => new Raw(s);
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ESC[c]);
const out = (v) =>
  v == null || v === false ? "" : v instanceof Raw ? v.s : Array.isArray(v) ? v.map(out).join("") : esc(v);
const html = (strings, ...vals) => raw(strings.reduce((acc, s, i) => acc + out(vals[i - 1]) + s));
const md = (s) => raw(esc(s ?? "").replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>"));
const setHTML = (el, frag) => { el.innerHTML = out(frag); };

/* ---------------- Storage (may be blocked) ---------------- */
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* ignore */ } },
  del(k) { try { localStorage.removeItem(k); } catch { /* ignore */ } },
};
const K_TEAM = "fpl-advisor.team";
const K_LEAGUE = "fpl-advisor.league";

/* ---------------- Formatting ---------------- */
const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const f1 = (v) => (isNum(v) ? v.toFixed(1) : "–");
const int = (v) => (isNum(v) ? Math.round(v).toLocaleString() : "–");
const money = (v) => (isNum(v) ? `£${v.toFixed(1)}m` : "–");
const pct = (v) => (isNum(v) ? `${Math.round(v * 100)}%` : "–");
const clamp01 = (v) => Math.max(0, Math.min(1, isNum(v) ? v : 0));
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
function ordinal(n) {
  const s = ["th", "st", "nd", "rd"], v = n % 100;
  return n.toLocaleString() + (s[(v - 20) % 10] || s[v] || s[0]);
}
const dateFmt = new Intl.DateTimeFormat(undefined, {
  weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
});
function countdown(ms) {
  if (ms <= 0) return "Deadline passed";
  const s = Math.floor(ms / 1000), d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600),
    m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m ${String(sec).padStart(2, "0")}s`;
}
function horizonLabel(h) {
  if (!Array.isArray(h) || !h.length) return "the next few gameweeks";
  return h.length === 1 ? `GW${h[0]}` : `GW${h[0]}–${h[h.length - 1]}`;
}

/* ---------------- ID parsing ---------------- */
const ENTRY_RE = /\/entry\/(\d{1,8})(?!\d)/i;
const LEAGUE_RE = /\/leagues\/(\d{1,8})(?!\d)/i;
const PLAIN_RE = /^#?(\d{1,8})$/;

export function parseIds(teamRaw, leagueRaw) {
  const t = String(teamRaw || "").trim(), l = String(leagueRaw || "").trim();
  // IDs are positive whole numbers: "0" and "000" count as not found.
  const pos = (v) => (v && Number(v) > 0 ? v : undefined);
  const plain = (s) => pos((s.replace(/[\s,.]/g, "").match(PLAIN_RE) || [])[1]);
  const team = pos(t.match(ENTRY_RE)?.[1]) ?? plain(t) ?? pos(l.match(ENTRY_RE)?.[1]);
  const league = pos(l.match(LEAGUE_RE)?.[1]) ?? plain(l) ?? pos(t.match(LEAGUE_RE)?.[1]);
  const errors = {};
  if (!team) {
    if (!t) errors.team = "Enter your team ID, or paste the link to your Points page.";
    else if (LEAGUE_RE.test(t)) errors.team = "That's a league link. We've moved it to the league box. Now add your team: open your Points page and paste that link here.";
    else errors.team = "We couldn't find a team ID in that. Type the number, or paste the link to your Points page (it contains /entry/).";
  }
  if (l && !league && !ENTRY_RE.test(l)) {
    errors.league = "We couldn't find a league ID in that. Type the number, or paste your league standings link (it contains /leagues/).";
  }
  return { team: team ? Number(team) : null, league: league ? Number(league) : null, errors };
}

/* ---------------- State ---------------- */
const state = {
  data: null,     // last advice
  demo: false,
  team: null,     // ids of the last successful (non-demo) load
  league: null,
  reqSeq: 0,
  timers: [],
};

/* ---------------- Form ---------------- */
const form = $("#finder");
const teamIn = $("#team");
const leagueIn = $("#league");
const goBtn = $("#go");

function showFieldError(input, errEl, msg) {
  errEl.textContent = msg || "";
  errEl.hidden = !msg;
  if (msg) input.setAttribute("aria-invalid", "true");
  else input.removeAttribute("aria-invalid");
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const { team, league, errors } = parseIds(teamIn.value, leagueIn.value);
  if (league && !errors.league) leagueIn.value = String(league);
  if (team) teamIn.value = String(team);
  else if (LEAGUE_RE.test(teamIn.value)) teamIn.value = ""; // league link pasted in the team box: moved
  if (!league && ENTRY_RE.test(leagueIn.value)) leagueIn.value = ""; // team link pasted in the league box: moved
  showFieldError(teamIn, $("#team-err"), errors.team);
  showFieldError(leagueIn, $("#league-err"), errors.league);
  if (errors.team) { teamIn.focus(); return; }
  if (errors.league) { leagueIn.focus(); return; }
  setQuery({ team, league });
  loadAdvice(team, league);
});

function setQuery(params) {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v != null) qs.set(k, String(v));
  const url = `${location.pathname}${qs.toString() ? `?${qs}` : ""}`;
  try { history.replaceState(null, "", url); } catch { /* ignore */ }
}

/* ---------------- Loading / errors ---------------- */
const statusEl = $("#status");
const resultsEl = $("#results");

function revealStatus() {
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const r = statusEl.getBoundingClientRect();
  if (r.top < 0 || r.bottom > innerHeight) statusEl.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "center" });
}

function clearTimers() {
  state.timers.forEach(clearTimeout);
  state.timers = [];
}

function showLoading() {
  clearTimers();
  resultsEl.hidden = true;
  document.body.classList.remove("has-results");
  setHTML(statusEl, html`<div class="status-panel" role="status">
    <span class="progress" aria-hidden="true"><span></span></span>
    <p class="kicker">Building your briefing</p>
    <p class="title" id="load-title">Working out your best moves…</p>
    <p class="muted" id="load-sub">Reading your squad and this week's fixtures.</p>
    <div class="skeleton" aria-hidden="true"><div></div><div></div><div></div></div>
  </div>`);
  revealStatus();
  state.timers.push(setTimeout(() => {
    const t = $("#load-title"), s = $("#load-sub");
    if (t) t.textContent = "Fetching the latest FPL data…";
    if (s) s.textContent = "The first load can take up to a minute. After that it's quick.";
  }, 5000));
  state.timers.push(setTimeout(() => {
    const s = $("#load-sub");
    if (s) s.textContent = "Still working. FPL's servers can be slow near a deadline. Thanks for waiting.";
  }, 35000));
}

function showError(message) {
  clearTimers();
  resultsEl.hidden = true;
  document.body.classList.remove("has-results");
  setHTML(statusEl, html`<div class="status-panel error" role="alert">
    <p class="kicker">Something's not right</p>
    <p class="title">We couldn't get your advice</p><p>${message}</p>
  </div>`);
  revealStatus();
}

function errorMessage(status, body) {
  if (status === 429) return "Too many requests. Please wait a minute and try again.";
  if (body && typeof body.error === "string" && body.error) return body.error;
  if (status === 422 || (body && Array.isArray(body.detail))) {
    return "That ID doesn't look right. Team and league IDs are whole numbers, up to 8 digits. Check the link you copied.";
  }
  if (status === 404) return "We couldn't find that team or league. Check the ID (it's the number in your FPL link).";
  if (status >= 500) return "Something went wrong on our side. Please try again in a minute.";
  return "Something went wrong. Please try again.";
}

async function loadAdvice(team, league) {
  const seq = ++state.reqSeq;
  showLoading();
  goBtn.disabled = true;
  const qs = new URLSearchParams({ team: String(team) });
  if (league) qs.set("league", String(league));
  const ctrl = new AbortController();
  const timeout = setTimeout(() => ctrl.abort(), 120000);
  let res, body;
  try {
    res = await fetch(`/api/advice?${qs}`, { signal: ctrl.signal, headers: { Accept: "application/json" } });
    body = await res.json().catch(() => null);
  } catch (e) {
    if (seq !== state.reqSeq) return;
    goBtn.disabled = false;
    showError(e && e.name === "AbortError"
      ? "This is taking too long. FPL may be busy right now. Please try again in a minute."
      : "We couldn't reach the server. Check your connection and try again.");
    return;
  } finally {
    clearTimeout(timeout);
  }
  if (seq !== state.reqSeq) return;
  goBtn.disabled = false;
  if (!res.ok || !body || typeof body !== "object") {
    showError(errorMessage(res.status, body));
    return;
  }
  store.set(K_TEAM, String(team));
  if (league) store.set(K_LEAGUE, String(league)); else store.del(K_LEAGUE);
  state.team = team;
  state.league = league || null;
  showResults(body, false);
}

async function loadDemo() {
  const seq = ++state.reqSeq;
  showLoading();
  try {
    const res = await fetch("/static/sample_advice.json");
    if (!res.ok) throw new Error(String(res.status));
    const body = await res.json();
    if (seq !== state.reqSeq) return;
    // Keep the sample countdown live: put the deadline the same distance ahead as in the sample.
    if (body.gameweek && isNum(body.gameweek.hours_to_deadline)) {
      body.gameweek = { ...body.gameweek, deadline: new Date(Date.now() + body.gameweek.hours_to_deadline * 3600e3).toISOString() };
    }
    showResults(body, true);
  } catch {
    if (seq === state.reqSeq) showError("We couldn't load the example just now. Please refresh the page.");
  }
}

function showResults(data, demo) {
  clearTimers();
  state.data = data;
  state.demo = demo;
  setHTML(statusEl, "");
  try {
    setHTML(resultsEl, renderResults(data, demo));
  } catch (e) {
    console.error(e);
    showError("We got your advice but couldn't display it. Please refresh and try again.");
    return;
  }
  resultsEl.hidden = false;
  document.body.classList.add("has-results");
  startCountdown();
  const title = $("#results-title");
  if (title) {
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    title.focus({ preventScroll: true });
    resultsEl.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  }
}

/* ---------------- Countdown ---------------- */
let tick = null;
function startCountdown() {
  clearInterval(tick);
  const update = () => {
    document.querySelectorAll("[data-deadline]").forEach((el) => {
      const ms = Date.parse(el.getAttribute("data-deadline")) - Date.now();
      if (!Number.isFinite(ms)) return;
      el.textContent = countdown(ms);
      el.closest(".deadline")?.classList.toggle("urgent", ms > 0 && ms < 3 * 3600e3);
    });
  };
  update();
  tick = setInterval(update, 1000);
}

/* ---------------- Player helpers ---------------- */
const POS = ["GKP", "DEF", "MID", "FWD"];
const POS_LABEL = { GKP: "Goalkeepers", DEF: "Defenders", MID: "Midfielders", FWD: "Forwards" };

function availability(p) {
  if (!p) return null;
  const doubtful = (p.status && p.status !== "a") || (isNum(p.chance) && p.chance < 100);
  if (!doubtful) return null;
  const bad = ["i", "s", "u", "n"].includes(p.status) || p.chance === 0;
  const text = p.news || (isNum(p.chance) ? `${p.chance}% chance of playing` : "Availability in doubt");
  return { kind: bad ? "bad" : "warn", text };
}

function priceBadge(p) {
  if (p?.price_move === "rise") return html`<span class="pill good"><span aria-hidden="true">▲</span> Price likely to rise tonight</span>`;
  if (p?.price_move === "fall") return html`<span class="pill bad"><span aria-hidden="true">▼</span> Price likely to fall tonight</span>`;
  return "";
}

function signalBadges(p, selling) {
  const out = [];
  if (!selling && isNum(p.p_goal)) out.push(html`<span class="pill odds" title="Bookmakers' chance this player scores in the next match">${pct(p.p_goal)} to score</span>`);
  if (isNum(p.elite_own)) out.push(html`<span class="pill elite" title="Share of the most consistent managers who own this player">Elite own ${pct(p.elite_own)}</span>`);
  return out;
}

function newsBadge(p) {
  const a = availability(p);
  return a ? html`<span class="pill ${a.kind}">${a.text}</span>` : "";
}

/* ---------------- Sections ---------------- */
// Editorial section header: small-caps kicker over a condensed headline.
function secHead(id, kicker, title, extra) {
  return html`<header class="sec-head">
    ${kicker ? html`<p class="kicker">${kicker}</p>` : ""}
    <h2 id="${id}" class="sec-title">${title}</h2>
    ${extra || ""}
  </header>`;
}

// Small two-tone kit swatch for a club (decorative).
const kitDot = (team) => html`<span class="kitdot" style="${kitStyle(team)}" aria-hidden="true"></span>`;

function renderResults(d, demo) {
  return html`<div class="results">
    ${summary(d, demo)}
    <div class="cols">
      <div class="col col-main">
        <div class="o1">${moves(d)}</div>
        <div class="o4">${lineup(d)}</div>
      </div>
      <div class="col col-side">
        <div class="o2">${captain(d)}</div>
        <div class="o3">${chips(d)}</div>
        <div class="o5">${timing(d)}</div>
      </div>
    </div>
    ${league(d)}
    ${elite(d)}
    ${creatorRoom(d)}
    ${notes(d)}
  </div>`;
}

function summary(d, demo) {
  const t = d.team || {}, gw = d.gameweek || {};
  const newSquad = !!(d.transfers?.initial || d.initial);
  const deadline = Date.parse(gw.deadline);
  const chipNow = d.chip_now ? (d.chips || []).find((c) => c.play_now && (d.chip_now_code ? c.chip === d.chip_now_code : c.name === d.chip_now)) : null;
  return html`<section class="scoreboard" aria-labelledby="results-title">
    <div class="sb-top">
      <div class="who">
        <p class="kicker">${isNum(gw.id) ? `Gameweek ${gw.id} briefing` : "This week's briefing"}
          ${demo ? html`<span class="pill sample">Sample data</span>` : ""}</p>
        <h2 id="results-title" tabindex="-1">${t.name || "Your team"}</h2>
        <p class="who-meta">${[t.manager, isNum(t.id) && !demo ? `Team ${t.id}` : null].filter(Boolean).join(" · ")}</p>
      </div>
      ${Number.isFinite(deadline) ? html`<div class="deadline">
        <span class="label">Deadline in</span>
        <span class="countdown" data-deadline="${new Date(deadline).toISOString()}">${countdown(deadline - Date.now())}</span>
        <span class="local">${dateFmt.format(deadline)} <span class="sr-only">your local time</span></span>
      </div>` : ""}
    </div>
    ${demo ? html`<p class="sample-note">This is an example team with made-up names for managers. Enter your own team ID above to get your advice.</p>` : ""}
    <dl class="stats">
      <div class="stat"><dt>Total points</dt><dd>${int(t.total_points)}</dd></div>
      ${newSquad ? "" : html`<div class="stat"><dt>Free transfers</dt><dd>${isNum(t.free_transfers) ? t.free_transfers : "–"}</dd></div>
      <div class="stat"><dt>Squad value</dt><dd>${money(t.value)}</dd></div>
      <div class="stat"><dt>In the bank</dt><dd>${money(t.bank)}</dd></div>`}
    </dl>
    ${d.chip_now ? html`<div class="chip-banner" role="note">
      <span class="pill spot">Chip</span>
      <div><strong>Play your ${d.chip_now} this week</strong>${chipNow?.reason ? html`<p>${md(chipNow.reason)}</p>` : ""}</div>
    </div>` : ""}
  </section>`;
}

function sidePlayer(p, dir, why) {
  const isOut = dir === "out";
  const price = isOut && isNum(p.sell_price) ? `sells for ${money(p.sell_price)}` : `costs ${money(p.price)}`;
  const a = availability(p);
  // why_out often repeats the injury news; show it once.
  const showNews = a && !(why && p.news && why.trim() === p.news.trim());
  return html`<div class="side ${dir}">
    <span class="dir">${isOut ? "Sell" : "Buy"}</span>
    <span class="pname">${kitDot(p.team)}${p.name}</span>
    <span class="pmeta">${[p.team, p.pos, price].filter(Boolean).join(" · ")}</span>
    <div class="badges">${showNews ? html`<span class="pill ${a.kind}">${a.text}</span>` : ""}${priceBadge(p)}${signalBadges(p, isOut)}</div>
  </div>`;
}
const capitalise = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);

function moveWhy(m, i) {
  const o = m.out, n = m.in;
  const items = [];
  if (o && m.why_out) items.push(html`<div class="why-row"><dt>Why sell ${o.name}</dt><dd>${md(capitalise(m.why_out))}</dd></div>`);
  if (n && m.why_in) items.push(html`<div class="why-row"><dt>Why buy ${n.name}</dt><dd>${md(m.why_in)}</dd></div>`);
  const srcs = [o, n].filter((p) => p && p.team_news && safeUrl(p.team_news_source));
  if (!items.length && !srcs.length) return "";
  return html`<details class="why" ${i === 0 ? "open" : ""}>
    <summary>The reasoning</summary>
    <dl class="why-list">${items}</dl>
    ${srcs.map((p) => html`<a class="src" href="${safeUrl(p.team_news_source)}" target="_blank" rel="noopener noreferrer">Team news source: ${p.name}</a>`)}
  </details>`;
}

function moves(d) {
  const tr = d.transfers;
  if (!tr) return "";
  const hz = horizonLabel(d.horizon);
  if (tr.initial) {
    const squad = Array.isArray(tr.squad) ? tr.squad : [];
    return html`<section class="sec sec-lead" aria-labelledby="moves-h">
      ${secHead("moves-h", "01 · Squad", "Pick your squad", tr.headline ? html`<p class="lede">${md(tr.headline)}</p>` : "")}
      <div class="squad-groups">
        ${POS.map((pos) => {
          const ps = squad.filter((p) => p.pos === pos);
          if (!ps.length) return "";
          return html`<div><h3 class="sub">${POS_LABEL[pos]}</h3><ul class="plist">${ps.map((p) => html`<li>
            <div class="left"><span class="pname">${kitDot(p.team)}${p.name}</span><span class="pmeta">${p.team} · next ${p.next_fixture || "–"}</span>
              <div class="badges">${newsBadge(p)}${priceBadge(p)}</div></div>
            <div class="right"><b>${money(p.price)}</b><span class="muted">${f1(p.xp_horizon)} xP ${hz}</span></div>
          </li>`)}</ul></div>`;
        })}
      </div>
      ${isNum(tr.bank_after) ? html`<div class="foot-notes"><p>Money left after buying this squad: <strong>${money(tr.bank_after)}</strong></p></div>` : ""}
    </section>`;
  }
  const list = Array.isArray(tr.moves) ? tr.moves : [];
  return html`<section class="sec sec-lead" aria-labelledby="moves-h">
    ${secHead("moves-h", "01 · Transfers", "Your moves this week", tr.headline ? html`<p class="lede">${md(tr.headline)}</p>` : "")}
    ${list.length ? html`<ol class="moves">${list.map((m, i) => html`<li class="move">
      <div class="move-top">
        <span class="label">Transfer ${i + 1}</span>
        ${isNum(m.gain) ? html`<span class="gain"><b>${m.gain >= 0 ? "+" : "−"}${f1(Math.abs(m.gain))}</b><small>pts expected, ${hz}</small></span>` : ""}
      </div>
      <div class="swap">
        ${m.out ? sidePlayer(m.out, "out", m.why_out) : ""}
        <div class="arrow" aria-hidden="true"></div>
        ${m.in ? sidePlayer(m.in, "in", m.why_in) : ""}
      </div>
      ${moveWhy(m, i)}
    </li>`)}</ol>` : html`<p class="empty-line">No transfers needed this week. Roll your free transfer and you'll have more to use next week.</p>`}
    <div class="foot-notes">
      ${tr.hit_note ? html`<p class="callout">${md(tr.hit_note)}</p>` : ""}
      ${isNum(tr.hits) && tr.hits > 0 && !tr.hit_note ? html`<p class="callout">This plan costs ${tr.hit_cost} points in hits (${plural(tr.hits, "extra transfer", "extra transfers")}).</p>` : ""}
      ${isNum(tr.bank_after) && list.length ? html`<p class="muted">Money in the bank after these moves: <strong>${money(tr.bank_after)}</strong></p>` : ""}
    </div>
  </section>`;
}

function captain(d) {
  const c = d.captain;
  if (!c || !c.captain) return "";
  const mult = isNum(c.multiplier) ? c.multiplier : 2;
  const capRow = (p, vice) => html`<div class="cap ${vice ? "vice" : "main"}">
    <span class="armband ${vice ? "v" : ""}" aria-hidden="true">${vice ? "V" : "C"}</span>
    <div class="info">
      <span class="role">${vice ? "Vice-captain" : mult === 3 ? "Captain (Triple Captain)" : "Captain"}</span>
      <span class="pname">${p.name}</span>
      <span class="pmeta">${[p.team, p.next_fixture ? `v ${p.next_fixture}` : null].filter(Boolean).join(" · ")}</span>
    </div>
    <div class="xp">${vice
      ? html`<b>${f1(p.xp_next)}</b><span>xP</span>`
      : html`<b>${f1(p.xp_next * mult)}</b><span>${f1(p.xp_next)} xP ×${mult}</span>`}</div>
  </div>`;
  const opts = Array.isArray(c.options) ? c.options : [];
  return html`<section class="sec" aria-labelledby="cap-h">
    ${secHead("cap-h", "02 · Armband", "Captain", html`<p class="aside">Your captain scores ${mult === 3 ? "triple" : "double"} points. The vice takes over if the captain doesn't play.</p>`)}
    <div class="cap-pair">${capRow(c.captain, false)}${c.vice ? capRow(c.vice, true) : ""}</div>
    ${c.note ? html`<p class="note">${md(c.note)}</p>` : ""}
    ${opts.length ? html`<h3 class="sub">Top options this week</h3>
      <ol class="plist ranked">${opts.map((p, i) => html`<li>
        <div class="left"><span class="pname"><span class="rank">${i + 1}</span>${kitDot(p.team)}${p.name}</span><span class="pmeta">${p.team} · v ${p.next_fixture || "–"}</span></div>
        <div class="right"><b>${f1(p.xp_next)}</b><span class="muted">xP</span></div>
      </li>`)}</ol>` : ""}
  </section>`;
}

// Home shirt colour per club short name, for the pitch view. Unknown clubs get a neutral shirt.
const KITS = {
  ARS: ["#e2231a", "#fff"], AVL: ["#7a1c45", "#fbd04a"], BOU: ["#d9261c", "#111"], BRE: ["#e30613", "#fff"],
  BHA: ["#0057b8", "#fff"], BUR: ["#6c1d45", "#9bd3f2"], CHE: ["#034694", "#fff"], CRY: ["#1b458f", "#e2231a"],
  EVE: ["#003399", "#fff"], FUL: ["#f5f5f5", "#111"], IPS: ["#2a5ba8", "#fff"], LEE: ["#f5f5f5", "#1d428a"],
  LEI: ["#0053a0", "#fdbe11"], LIV: ["#c8102e", "#fff"], MCI: ["#6cabdd", "#0c2340"], MUN: ["#da291c", "#fff"],
  NEW: ["#241f20", "#fff"], NFO: ["#dd0000", "#fff"], SOU: ["#d71920", "#fff"], SUN: ["#eb172b", "#fff"],
  TOT: ["#f5f5f5", "#132257"], WHU: ["#7a263a", "#7fc4e8"], WOL: ["#fdb913", "#231f20"],
};
const kitStyle = (team) => {
  const k = KITS[String(team || "").toUpperCase()];
  return k ? `--kit:${k[0]};--kit-ink:${k[1]}` : "";
};

function pitchChip(p, { capId, viceId, order, fresh } = {}) {
  const a = availability(p);
  const cv = p.id === capId ? "C" : p.id === viceId ? "V" : null;
  const isNew = fresh && fresh.has(p.id);
  const label = [p.name, p.team, p.next_fixture ? `v ${p.next_fixture}` : null, `${f1(p.xp_next)} expected points`,
    cv === "C" ? "captain" : cv === "V" ? "vice-captain" : null, isNew ? "suggested signing" : null,
    a ? `Team news: ${a.text}` : null].filter(Boolean).join(", ");
  return html`<li class="pl ${isNew ? "is-new" : ""}" title="${a ? a.text : ""}">
    <span class="sr-only">${order ? `Bench ${order}: ` : ""}${label}</span>
    ${cv ? html`<span class="cv ${cv === "V" ? "v" : ""}" aria-hidden="true">${cv}</span>` : ""}
    ${a ? html`<span class="flag ${a.kind}" aria-hidden="true"></span>` : ""}
    ${isNew ? html`<span class="newtag" aria-hidden="true">In</span>` : ""}
    <span class="kit" style="${kitStyle(p.team)}" aria-hidden="true">${p.team || "–"}</span>
    <span class="nm" aria-hidden="true">${p.name}</span>
    <span class="fx" aria-hidden="true">${p.next_fixture || "–"}</span>
    <span class="xpn" aria-hidden="true">${f1(p.xp_next)}</span>
    ${order ? html`<span class="order" aria-hidden="true">${order}</span>` : ""}
  </li>`;
}

// Only web links reach an href: anything else (javascript:, data:, junk) becomes null.
function safeUrl(u) {
  try {
    const x = new URL(String(u), location.href);
    return ["https:", "http:"].includes(x.protocol) ? x.href : null;
  } catch { return null; }
}

// One team sheet: the pitch, the bench and any team news for it.
function sheet(L, { fresh, benchBoost } = {}) {
  const opts = { capId: L.captain_id, viceId: L.vice_id, fresh };
  const bench = Array.isArray(L.bench) ? L.bench : [];
  const flagged = [...L.xi, ...bench].filter((p) => availability(p));
  return html`<div class="pitch">
      ${POS.map((pos) => {
        const row = L.xi.filter((p) => p.pos === pos);
        return row.length ? html`<ul class="prow" aria-label="${POS_LABEL[pos]}">${row.map((p) => pitchChip(p, opts))}</ul>` : "";
      })}
    </div>
    ${bench.length ? html`<div class="bench">
      <h3 class="sub">${benchBoost ? "Bench (Bench Boost on: these score too)" : "Bench, in order"}</h3>
      <ul class="prow" aria-label="Bench in order">${bench.map((p, i) => pitchChip(p, { ...opts, order: i + 1 }))}</ul>
    </div>` : ""}
    ${flagged.length ? html`<ul class="news-list" aria-label="Team news">${flagged.map((p) => {
      const a = availability(p);
      return html`<li><span class="dot ${a.kind}" aria-hidden="true"></span><span><strong>${p.name}</strong>: ${a.text}</span></li>`;
    })}</ul>` : ""}`;
}

// Two views of the team sheet: as the manager left it (the default, since nobody has to take our
// advice) and after the suggested moves, with the expected points of each.
function lineup(d) {
  const L = d.lineup;
  if (!L || !Array.isArray(L.xi) || !L.xi.length) return "";
  const C = L.current && Array.isArray(L.current.xi) && L.current.xi.length ? L.current : null;
  const after = sheet(L, {
    fresh: C ? new Set(L.xi.concat(L.bench || []).map((p) => p.id).filter((id) => !C.xi.concat(C.bench || []).some((q) => q.id === id))) : null,
    benchBoost: L.bench_boost,
  });
  if (!C) {
    return html`<section class="sec" aria-labelledby="xi-h">
      ${secHead("xi-h", "Team sheet", "Your starting XI",
        isNum(L.expected_points) ? html`<p class="aside xp-total">Expected points this week <b>${f1(L.expected_points)}</b></p>` : "")}
      ${after}
    </section>`;
  }
  const gain = isNum(L.expected_points) && isNum(C.expected_points) ? L.expected_points - C.expected_points : null;
  const view = state.sheet === "after" ? "after" : "now";
  const tab = (key, label, pts) => html`<button type="button" class="seg-btn" data-sheet="${key}" aria-pressed="${view === key}">
      <span class="seg-label">${label}</span>
      <span class="seg-pts">${isNum(pts) ? f1(pts) : "–"} <small>pts</small></span>
    </button>`;
  return html`<section class="sec" aria-labelledby="xi-h">
    ${secHead("xi-h", "Team sheet", "Your starting XI",
      gain !== null && gain > 0.05 ? html`<p class="aside xp-total">Our moves add <b>+${f1(gain)}</b> expected points this week</p>` : "")}
    <div class="seg" role="group" aria-label="Which team sheet to show">
      ${tab("now", "Your team now", C.expected_points)}
      ${tab("after", "After our moves", L.expected_points)}
    </div>
    <p class="hint sheet-note" data-sheet-note="now" ${view === "now" ? "" : "hidden"}>The XI, bench and captain you last saved in FPL. It's what scores if you change nothing.</p>
    <p class="hint sheet-note" data-sheet-note="after" ${view === "after" ? "" : "hidden"}>Your team with the transfers, captain and chips suggested above. Players marked <span class="newtag inline">In</span> are the suggested signings.</p>
    <div data-sheet-view="now" ${view === "now" ? "" : "hidden"}>${sheet(C)}</div>
    <div data-sheet-view="after" ${view === "after" ? "" : "hidden"}>${after}</div>
  </section>`;
}

document.addEventListener("click", (e) => {
  const btn = e.target.closest?.("[data-sheet]");
  if (!btn) return;
  const sec = btn.closest("section");
  state.sheet = btn.dataset.sheet;
  sec.querySelectorAll("[data-sheet]").forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
  sec.querySelectorAll("[data-sheet-view],[data-sheet-note]").forEach((el) => {
    el.hidden = (el.dataset.sheetView || el.dataset.sheetNote) !== state.sheet;
  });
});

function chips(d) {
  const list = Array.isArray(d.chips) ? d.chips : [];
  if (!list.length) return "";
  return html`<section class="sec" aria-labelledby="chips-h">
    ${secHead("chips-h", "03 · Chips", "Chips")}
    <ul class="chips">${list.map((c) => html`<li class="chiprow ${c.play_now ? "now" : ""}">
      <div class="head">
        <span class="name">${c.name || c.chip}</span>
        ${c.play_now ? html`<span class="pill spot">Play now</span>` : html`<span class="pill">Keep</span>`}
        ${isNum(c.expires_gw) ? html`<span class="exp">Expires after GW${c.expires_gw}</span>` : ""}
      </div>
      ${c.reason ? html`<details class="why" ${c.play_now ? "open" : ""}><summary>${c.play_now ? "Why now" : "Why wait"}</summary><p>${md(c.reason)}</p></details>` : ""}
    </li>`)}</ul>
  </section>`;
}

function timing(d) {
  const list = Array.isArray(d.timing) ? d.timing.filter(Boolean) : [];
  if (!list.length) return "";
  return html`<section class="sec" aria-labelledby="timing-h">
    ${secHead("timing-h", "Prices", "When to make your moves")}
    <ul class="ticks">${list.map((t) => html`<li><span>${md(t)}</span></li>`)}</ul>
  </section>`;
}

function ownList(players, empty, partial) {
  if (!Array.isArray(players) || !players.length) return html`<p class="empty-line">${empty}</p>`;
  return html`<ul class="own-list">${players.map((p) => html`<li>
    <div class="left"><span class="pname">${kitDot(p.team)}${p.name}</span> <span class="pmeta">${[p.team, p.pos].filter(Boolean).join(" · ")}</span></div>
    <div class="own" aria-label="Owned by ${pct(p.ownership)} of ${partial ? "the rivals we track" : "your rivals"}">
      <span class="bar" aria-hidden="true"><span style="width:${Math.round(clamp01(p.ownership) * 100)}%"></span></span>
      <span aria-hidden="true">${pct(p.ownership)}</span>
    </div>
  </li>`)}</ul>`;
}

function league(d) {
  const L = d.league;
  if (!L) return "";
  const me = d.team?.total_points;
  const table = Array.isArray(L.table) ? L.table : [];
  const rows = [];
  table.forEach((r, i) => {
    if (i > 0 && isNum(r.rank) && isNum(table[i - 1].rank) && r.rank - table[i - 1].rank > 1) {
      rows.push(html`<tr class="gap" aria-hidden="true"><td colspan="4">…</td></tr>`);
    }
    rows.push(html`<tr class="${r.is_me ? "me" : ""}">
      <td class="n">${r.rank}</td>
      <td>${r.manager}${r.is_me ? html` <span class="sr-only">(you)</span>` : ""}<span class="team">${r.team_name}</span></td>
      <td class="n">${int(r.event_total)}</td>
      <td class="n">${int(r.total)}</td>
    </tr>`);
  });
  const lead = isNum(L.gap_to_leader) && L.gap_to_leader <= 0;
  const second = table.find((r) => !r.is_me);
  const leadBy = lead && second && isNum(me) && isNum(second.total) ? me - second.total : null;
  const onPace = isNum(L.my_average) && isNum(L.required_per_gw) && L.my_average >= L.required_per_gw;
  const rivalChips = (Array.isArray(L.rival_chips) ? L.rival_chips : []).filter((r) => r && Array.isArray(r.chips));
  const total = isNum(L.total_managers) && L.total_managers > 0 ? L.total_managers : null;
  const partial = total != null && isNum(L.tracked) && L.tracked < total;

  return html`<section class="sec sec-wide" aria-labelledby="league-h">
    ${secHead("league-h", "Mini-league", L.name || "Your league",
      isNum(L.rank) ? html`<p class="lede">You're <strong>${ordinal(L.rank)}</strong>${total != null ? ` of ${int(total)}` : ""}${
        lead ? (isNum(leadBy) ? `, ${plural(leadBy, "point", "points")} clear at the top.` : ", top of the league.")
        : isNum(L.gap_to_leader) ? `, ${plural(L.gap_to_leader, "point", "points")} behind the leader.` : "."}</p>` : "")}
    <div class="grid2">
      <div class="stack">
        ${isNum(L.target) ? html`<div class="target">
          <div class="target-head"><span>Points likely needed to win</span><b>${int(L.target)}</b></div>
          ${isNum(me) ? html`<div class="bar" role="img" aria-label="${int(me)} of ${int(L.target)} points so far"><span style="width:${Math.round(clamp01(me / L.target) * 100)}%"></span></div>
          <p class="small muted">${int(me)} so far${isNum(L.remaining_gws) ? ` · ${int(Math.max(0, L.target - me))} to go in ${plural(L.remaining_gws, "gameweek", "gameweeks")}` : ""}</p>` : ""}
          ${isNum(L.required_per_gw) ? html`<div class="pace">
            <div><span>Needed per gameweek</span><b>${f1(L.required_per_gw)}</b></div>
            <div><span>Your average</span><b>${f1(L.my_average)}</b></div>
          </div>
          <p class="small">${onPace
            ? html`<span class="pill good">On pace</span> Keep scoring like this and you reach the target.`
            : html`<span class="pill warn">Behind pace</span> You need about ${f1(L.required_per_gw - L.my_average)} more points a week.`}</p>` : ""}
          ${L.last_season_best ? html`<p class="small muted">Best total last season among these managers: ${int(L.last_season_best.points)} (${L.last_season_best.manager}).</p>` : ""}
        </div>` : ""}
        ${L.strategy ? html`<div><h3 class="sub first">Your strategy</h3><p class="small">${md(L.strategy)}</p></div>` : ""}
      </div>
      <div>
        <h3 class="sub first">Standings</h3>
        <div class="table-wrap" tabindex="0" role="region" aria-label="League standings">
          <table>
            <thead><tr><th class="n" scope="col">#</th><th scope="col">Manager</th><th class="n" scope="col">GW</th><th class="n" scope="col">Total</th></tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </div>
    </div>
    <div class="grid2">
      <div>
        <h3 class="sub">Popular with rivals, not in your team</h3>
        ${ownList(L.threats, "None of your rivals' popular picks are missing from your team.", partial)}
      </div>
      <div>
        <h3 class="sub">Your differentials</h3>
        ${ownList(L.differentials, "No differentials in your XI this week.", partial)}
      </div>
    </div>
    ${partial ? html`<p class="small muted gap-top">Ownership is shown as a share of the rivals we track (top 10 and those near you), not the whole league.</p>` : ""}
    ${rivalChips.length ? html`<h3 class="sub">Rivals' chips still to play</h3>
      <ul class="rivals">${rivalChips.map((r) => html`<li><span class="mgr">${r.manager}</span><span class="rchips">${
        r.chips.length ? r.chips.map((c) => html`<span class="pill">${c}</span>`) : html`<span class="muted">none left</span>`}</span></li>`)}</ul>` : ""}
  </section>`;
}

function eliteRow(p, kind) {
  const val = kind === "own" ? pct(p.elite_own)
    : kind === "in" ? `+${p.elite_in ?? 0}` : `−${p.elite_out ?? 0}`;
  return html`<li>
    <div class="left"><span class="pname">${kitDot(p.team)}${p.name}</span> <span class="pmeta">${[p.team, p.pos, money(p.price)].filter(Boolean).join(" · ")}</span>
      ${p.in_your_team ? html`<span class="badges"><span class="pill good">In your team</span></span>` : ""}</div>
    <div class="right"><b>${val}</b>${kind === "own" && isNum(p.elite_captain) && p.elite_captain > 0.05 ? html`<span class="muted">${pct(p.elite_captain)} captain</span>` : ""}</div>
  </li>`;
}

function elite(d) {
  const E = d.elite;
  if (!E || !Array.isArray(E.most_owned) || !E.most_owned.length) return "";
  const seasons = Array.isArray(E.seasons) && E.seasons.length ? `${E.seasons[0]} to ${E.seasons[E.seasons.length - 1]}` : "the last three seasons";
  const list = (rows, kind, empty) => rows?.length ? html`<ul class="plist">${rows.map((p) => eliteRow(p, kind))}</ul>` : html`<p class="empty-line">${empty}</p>`;
  return html`<section class="sec sec-wide" aria-labelledby="elite-h">
    ${secHead("elite-h", "The elite", "What the most consistent managers are doing",
      html`<p class="lede">The ${E.managers} managers with the best typical finish over ${seasons}${isNum(E.median_rank) ? html` (a typical finish of about <strong>${ordinal(Math.round(E.median_rank))}</strong> overall)` : ""}. Their teams are public only after each deadline, so this is from gameweek ${E.gameweek}.</p>`)}
    <div class="grid2">
      <div><h3 class="sub first">Most owned</h3>${list(E.most_owned, "own", "No data yet.")}</div>
      <div>
        <h3 class="sub first">Popular with them, missing from your team</h3>${list(E.missing, "own", "You already own all their favourites.")}
        <h3 class="sub">Bought last gameweek</h3>${list(E.bought, "in", "No clear buys.")}
        <h3 class="sub">Sold last gameweek</h3>${list(E.sold, "out", "No clear sales.")}
      </div>
    </div>
    <p class="small muted gap-top">This shows what good managers do. It doesn't decide your moves: the advice above already weighs fixtures, form, odds and team news.</p>
  </section>`;
}

/* Creator room: what big FPL YouTubers plan. Informational only; links go through safeUrl. */
const CHIP_LABEL = { wildcard: "Wildcard", free_hit: "Free Hit", bench_boost: "Bench Boost", triple_captain: "Triple Captain" };
const isRef = (p) => p && typeof p === "object" && typeof p.name === "string";
const refs = (xs) => (Array.isArray(xs) ? xs.filter(isRef) : []);

function pchip(p, extra) {
  return html`<span class="pchip ${extra || ""}" title="${[p.name, p.team].filter(Boolean).join(", ")}">
    ${kitDot(p.team)}${p.name}${p.team ? html` <span class="tm">${p.team}</span>` : ""}</span>`;
}

function chipGroup(label, items) {
  return items.length ? html`<div class="cgroup"><span class="clabel">${label}</span><span class="citems">${items}</span></div>` : "";
}

function shortDate(iso) {
  const t = new Date(String(iso || ""));
  return Number.isNaN(t.getTime()) ? "" : t.toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short" });
}

function creatorRow(c, R) {
  const v = c.video && typeof c.video === "object" ? c.video : null;
  const vurl = v ? safeUrl(v.url) : null;
  const curl = safeUrl(c.channel_url);
  const decidedIn = refs(c.decided_in), decidedOut = refs(c.decided_out);
  const ids = (xs) => new Set(xs.map((p) => p.id));
  const dIn = ids(decidedIn), dOut = ids(decidedOut);
  const buying = [...decidedIn.map((p) => pchip(p, "in")), ...refs(c.considering_in).filter((p) => !dIn.has(p.id)).map((p) => pchip(p, "in maybe"))];
  const selling = [...decidedOut.map((p) => pchip(p, "out")), ...refs(c.considering_out).filter((p) => !dOut.has(p.id)).map((p) => pchip(p, "out maybe"))];
  const caps = isRef(c.captain) ? [pchip(c.captain, "cap")] : refs(c.captain_options).map((p) => pchip(p, "cap maybe"));
  const A = c.actual && typeof c.actual === "object" ? c.actual : null;
  return html`<li class="creator">
    <div class="c-head">
      ${curl ? html`<a class="c-name" href="${curl}" target="_blank" rel="noopener noreferrer">${c.name}</a>` : html`<span class="c-name">${c.name}</span>`}
      ${v && v.published ? html`<span class="muted small">${shortDate(v.published)}</span>` : ""}
      ${CHIP_LABEL[c.chip] ? html`<span class="pill odds">Playing ${CHIP_LABEL[c.chip]}</span>` : ""}
    </div>
    ${v ? (vurl ? html`<a class="c-video" href="${vurl}" target="_blank" rel="noopener noreferrer">${v.title}</a>` : html`<span class="c-video">${v.title}</span>`)
      : html`<p class="empty-line">No plan video for gameweek ${R.gameweek} yet.</p>`}
    ${c.summary ? html`<p class="c-sum">${c.summary}</p>` : v && R.read_by_llm && !c.read ? html`<p class="empty-line">We couldn't read a plan from this video's description. Watch the video for details.</p>` : ""}
    ${buying.length || selling.length || caps.length ? html`<div class="cgroups">
      ${chipGroup("Buying", buying)}${chipGroup("Selling", selling)}${chipGroup("Captain", caps)}</div>` : ""}
    ${A ? html`<div class="c-actual">
      <span class="clabel">Actually did in GW${A.gameweek}</span>
      <div class="cgroups">
        ${chipGroup("In", refs(A.transfers_in).map((p) => pchip(p, "in")))}
        ${chipGroup("Out", refs(A.transfers_out).map((p) => pchip(p, "out")))}
        ${isRef(A.captain) ? chipGroup("Captain", [pchip(A.captain, "cap")]) : ""}
        ${!refs(A.transfers_in).length && !refs(A.transfers_out).length ? html`<span class="muted small">No transfers.</span>` : ""}
        ${CHIP_LABEL[A.chip] ? html`<span class="pill odds">${CHIP_LABEL[A.chip]}</span>` : ""}
        ${isNum(A.hit) && A.hit > 0 ? html`<span class="pill warn">−${A.hit} hit</span>` : ""}
      </div></div>` : ""}
  </li>`;
}

function creatorRoom(d) {
  const R = d.creators;
  if (!R || !Array.isArray(R.creators) || !R.creators.length) return "";
  const agree = Array.isArray(R.agree) ? R.agree : [];
  const split = Array.isArray(R.split) ? R.split : [];
  const n = R.creators.length;
  const enough = isNum(R.with_plan) && R.with_plan >= 2;
  const agreeList = agree.length ? html`<ul class="plist">${agree.map((a) => html`<li>
      <div class="left"><span>${a.text}</span>${isRef(a.player) && a.player.in_your_team ? html`<span class="badges"><span class="pill good">In your team</span></span>` : ""}</div>
      <div class="right"><b>${a.count}/${a.of}</b></div></li>`)}</ul>`
    : html`<p class="empty-line">No player has at least half of them behind it.</p>`;
  const splitList = split.length ? html`<ul class="plist">${split.map((x) => html`<li>
      <div class="left"><span>${x.text}</span>${isRef(x.player) && x.player.in_your_team ? html`<span class="badges"><span class="pill good">In your team</span></span>` : ""}</div></li>`)}</ul>`
    : html`<p class="empty-line">No clear disagreements.</p>`;
  return html`<section class="sec sec-wide" aria-labelledby="creators-h">
    ${secHead("creators-h", "Creator room", "What the big FPL YouTubers are planning",
      html`<p class="lede">From each creator's latest gameweek ${R.gameweek} plan video${R.read_by_llm ? ", read from its title and description" : ""}. ${plural(isNum(R.with_video) ? R.with_video : 0, "of " + n + " has", "of " + n + " have")} posted one so far.</p>`)}
    ${R.read_by_llm && enough ? html`<div class="grid2">
      <div><h3 class="sub first">They agree</h3>${agreeList}</div>
      <div><h3 class="sub first">They're split</h3>${splitList}</div>
    </div>` : ""}
    <h3 class="sub">Creator by creator</h3>
    <p class="small muted legend">Solid chips are decided moves; dashed chips are players they are still thinking about.</p>
    <ul class="creator-list">${R.creators.map((c) => creatorRow(c, R))}</ul>
    <p class="small muted gap-top">Creators watch each other's videos, so when they agree it is closer to one shared opinion than ${n} separate votes. Plans are summarised by an AI from the video text and can be wrong or out of date: watch the video before you act. This does not change the advice above.</p>
  </section>`;
}

// When each outside source was last refreshed (FPL data itself is live).
const SOURCE_LABEL = { odds: "Bookmaker odds", news: "Team news", elite: "Elite managers", creators: "Creator videos" };
function sourcesLine(d) {
  const s = d.sources && typeof d.sources === "object" ? d.sources : null;
  if (!s) return "";
  const parts = Object.keys(SOURCE_LABEL).map((k) => {
    const t = Date.parse(s[k]);
    return Number.isFinite(t) ? `${SOURCE_LABEL[k]} ${dateFmt.format(t)}` : null;
  }).filter(Boolean);
  return parts.length ? html`<p class="sources"><span class="clabel">Updated</span> FPL data live · ${parts.join(" · ")}</p>` : "";
}

function notes(d) {
  const list = Array.isArray(d.notes) ? d.notes.filter(Boolean) : [];
  return html`<section class="sec sec-wide" aria-labelledby="notes-h">
    ${secHead("notes-h", "Small print", "Good to know")}
    ${list.length ? html`<ul class="ticks">${list.map((n) => html`<li><span>${md(n)}</span></li>`)}</ul>` : ""}
    <details class="how">
      <summary>How this works</summary>
      <div>
        <p>Each player gets <strong>expected points (xP)</strong> for every upcoming gameweek. They come from their expected goals and assists (xG, xA), how many minutes they usually play, and how hard each fixture is.</p>
        <p>An optimiser then picks the transfers, lineup, captain and chips that add the most expected points over the next ${Array.isArray(d.horizon) && d.horizon.length ? d.horizon.length : 5} gameweeks, within your budget and FPL's rules. A hit (−4) is only suggested when it pays for itself.</p>
        <p>These are estimates, not promises. Football is random, so treat the advice as a strong second opinion and check team news before the deadline.</p>
      </div>
    </details>
    ${sourcesLine(d)}
  </section>`;
}

/* ---------------- Boot ---------------- */
function boot() {
  const params = new URLSearchParams(location.search);
  if (params.get("demo") === "1") {
    loadDemo();
    return;
  }
  const qTeam = params.get("team"), qLeague = params.get("league");
  if (qTeam) {
    const { team, league, errors } = parseIds(qTeam, qLeague || "");
    teamIn.value = team ? String(team) : qTeam;
    leagueIn.value = league ? String(league) : qLeague || "";
    if (team && !errors.league) { loadAdvice(team, league); return; }
    showFieldError(teamIn, $("#team-err"), errors.team);
    showFieldError(leagueIn, $("#league-err"), errors.league);
    return;
  }
  teamIn.value = store.get(K_TEAM) || "";
  leagueIn.value = store.get(K_LEAGUE) || "";
}

boot();
