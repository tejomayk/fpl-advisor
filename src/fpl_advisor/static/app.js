// FPL Advisor front end. No build step, no external requests.
// Every string from the API is untrusted: it only reaches the DOM through html`` (which escapes
// interpolations) or md() (escape first, then turn **pairs** into <strong>).

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

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
const signed = (v) => (isNum(v) ? `${v >= 0 ? "+" : "−"}${f1(Math.abs(v))}` : "–");
const reduceMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
function ordinal(n) {
  const s = ["th", "st", "nd", "rd"], v = n % 100;
  return n.toLocaleString() + (s[(v - 20) % 10] || s[v] || s[0]);
}
const dateFmt = new Intl.DateTimeFormat(undefined, {
  weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
});
const pad2 = (n) => String(n).padStart(2, "0");
// Score-bug clock: "2d 04:11:09", "11:47:03", or "Deadline passed".
function countdown(ms) {
  if (ms <= 0) return "Passed";
  const s = Math.floor(ms / 1000), d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600),
    m = Math.floor((s % 3600) / 60), sec = s % 60;
  return `${d > 0 ? `${d}d ` : ""}${pad2(h)}:${pad2(m)}:${pad2(sec)}`;
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
  sheet: "now",
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
  const r = statusEl.getBoundingClientRect();
  if (r.top < 0 || r.bottom > innerHeight) statusEl.scrollIntoView({ behavior: reduceMotion() ? "auto" : "smooth", block: "center" });
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
    <p class="status-kicker">Building your briefing</p>
    <p class="status-title" id="load-title">Working out your best moves…</p>
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
    <p class="status-kicker">Something's not right</p>
    <p class="status-title">We couldn't get your advice</p><p>${message}</p>
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
  state.sheet = "now";
  setHTML(statusEl, "");
  try {
    setHTML(resultsEl, renderResults(data, demo));
  } catch (e) {
    console.error(e);
    showError("We got your advice but couldn't display it. Please refresh and try again.");
    return;
  }
  resultsEl.hidden = false;
  document.body.classList.remove("finder-open");
  document.body.classList.add("has-results");
  startCountdown();
  countUp(resultsEl);
  const title = $("#results-title");
  if (title) {
    title.focus({ preventScroll: true });
    resultsEl.scrollIntoView({ behavior: reduceMotion() ? "auto" : "smooth", block: "start" });
  }
}

/* ---------------- Motion helpers ---------------- */
// Hero numbers count up from zero once (~400ms). The final text is already in the DOM, so with
// reduced motion (or no JS timing) nothing changes.
function countUp(root) {
  if (reduceMotion()) return;
  $$("[data-count]", root).forEach((el) => {
    const end = Number(el.dataset.count);
    if (!Number.isFinite(end)) return;
    const final = el.textContent;
    const sign = el.dataset.sign === "1";
    const t0 = performance.now(), dur = 420;
    const step = (t) => {
      const k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 3), v = end * e;
      el.textContent = k >= 1 ? final : `${sign ? (v >= 0 ? "+" : "−") : ""}${Math.abs(v).toFixed(1)}`;
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  });
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

/* ---------------- Club shirts ---------------- */
// Flat two-colour shirts per club short name: [body, accent, pattern]. Patterns: "plain",
// "sleeves" (accent sleeves), "stripes" (accent vertical stripes), "hoops" is not needed yet.
// Unknown clubs get a neutral shirt. No crests or logos: just colours and the 3-letter code.
const KITS = {
  ARS: ["#DB0007", "#FFFFFF", "sleeves"], AVL: ["#7A1C45", "#95BFE5", "sleeves"], BOU: ["#DA291C", "#111111", "stripes"],
  BRE: ["#E30613", "#FFFFFF", "stripes"], BHA: ["#0057B8", "#FFFFFF", "stripes"], BUR: ["#6C1D45", "#99D6EA", "sleeves"],
  CHE: ["#034694", "#FFFFFF", "plain"], CRY: ["#1B458F", "#C4122E", "stripes"], EVE: ["#003399", "#FFFFFF", "plain"],
  FUL: ["#F5F5F5", "#111111", "plain"], IPS: ["#1F4FA0", "#FFFFFF", "plain"], LEE: ["#F5F5F5", "#1D428A", "plain"],
  LEI: ["#0053A0", "#FDBE11", "plain"], LIV: ["#C8102E", "#F6EB61", "plain"], MCI: ["#6CABDD", "#FFFFFF", "plain"],
  MUN: ["#DA291C", "#111111", "plain"], NEW: ["#111111", "#FFFFFF", "stripes"], NFO: ["#DD0000", "#FFFFFF", "plain"],
  SOU: ["#D71920", "#FFFFFF", "stripes"], SUN: ["#EB172B", "#FFFFFF", "stripes"], TOT: ["#F5F5F5", "#132257", "plain"],
  WHU: ["#7A263A", "#7FC4E8", "sleeves"], WOL: ["#FDB913", "#231F20", "plain"], HUL: ["#F5A12D", "#111111", "stripes"],
  COV: ["#6CC4EE", "#FFFFFF", "plain"], MID: ["#DE1B22", "#FFFFFF", "plain"], SHU: ["#EE2737", "#FFFFFF", "stripes"],
  NOR: ["#FFF200", "#00A650", "plain"], WBA: ["#122F67", "#FFFFFF", "stripes"], WAT: ["#FBEE23", "#ED2127", "plain"],
  SHW: ["#1E62B5", "#FFFFFF", "stripes"], STK: ["#E03A3E", "#FFFFFF", "stripes"], LUT: ["#F78F1E", "#002D62", "plain"],
};
const NEUTRAL_KIT = ["#8A978F", "#FFFFFF", "plain"];
const kitOf = (team) => KITS[String(team || "").toUpperCase()] || NEUTRAL_KIT;
const SHIRT = "M22 6 L13 9.5 L3 19 L10.5 28.5 L15 24.5 L15 58 L49 58 L49 24.5 L53.5 28.5 L61 19 L51 9.5 L42 6 Q32 13 22 6 Z";

// size: px; label: draw the club code on the chest (only legible at 34px and up).
function shirt(team, size = 40, label = size >= 44) {
  const [body, accent, pat] = kitOf(team);
  const code = String(team || "").toUpperCase().slice(0, 3);
  const stripes = pat === "stripes"
    ? html`<path d="M19 9 h5 V58 h-5Z M29.5 12 h5 V58 h-5Z M40 9 h5 V58 h-5Z" fill="${accent}"/>` : "";
  const sleeves = pat === "sleeves"
    ? html`<path d="M15 8.8 L3 19 L10.5 28.5 L15 24.5Z M49 8.8 L61 19 L53.5 28.5 L49 24.5Z" fill="${accent}"/>` : "";
  return html`<svg class="shirt" viewBox="0 0 64 64" width="${size}" height="${size}" aria-hidden="true" focusable="false">
    <path d="${SHIRT}" fill="${body}"/>${stripes}${sleeves}
    <path d="M22 6 Q32 13 42 6" fill="none" stroke="${pat === "plain" ? accent : body}" stroke-width="3"/>
    <path d="${SHIRT}" fill="none" class="shirt-edge" stroke-width="1.2" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>
    ${label && code ? html`${pat === "stripes" ? html`<rect x="17" y="30" width="30" height="17" fill="${body}"/>` : ""}<text x="32" y="44" text-anchor="middle" class="shirt-code" fill="${accent}">${code}</text>` : ""}
  </svg>`;
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
  if (p?.price_move === "rise") return html`<span class="tag warn"><span aria-hidden="true">▲</span> Price likely to rise tonight</span>`;
  if (p?.price_move === "fall") return html`<span class="tag bad"><span aria-hidden="true">▼</span> Price likely to fall tonight</span>`;
  return "";
}

function signalBadges(p, selling) {
  const res = [];
  if (!selling && isNum(p.p_goal)) res.push(html`<span class="tag info" title="Bookmakers' chance this player scores in the next match">${pct(p.p_goal)} to score</span>`);
  if (isNum(p.elite_own)) res.push(html`<span class="tag info" title="Share of the most consistent managers who own this player">Elite own ${pct(p.elite_own)}</span>`);
  return res;
}

function newsBadge(p) {
  const a = availability(p);
  return a ? html`<span class="tag ${a.kind}">${a.text}</span>` : "";
}

// Only web links reach an href: anything else (javascript:, data:, junk) becomes null.
function safeUrl(u) {
  try {
    const x = new URL(String(u), location.href);
    return ["https:", "http:"].includes(x.protocol) ? x.href : null;
  } catch { return null; }
}

const capitalise = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);

/* ---------------- Sections ---------------- */
function secHead(id, title, extra) {
  return html`<header class="sec-head">
    <h2 id="${id}" class="sec-title">${title}</h2>
    ${extra || ""}
  </header>`;
}

// Unique ids for show-all toggles within one render.
let uid = 0;
const nextId = (p) => `${p}-${++uid}`;
// On phones, long tables show 5 rows; this button reveals the rest (hidden on wider screens).
const moreBtn = (id, n) => html`<button type="button" class="more-btn sm-only" data-reveal aria-controls="${id}" aria-expanded="false" data-more="Show all ${n}" data-less="Show fewer">Show all ${n}</button>`;

function renderResults(d, demo) {
  return html`<div class="results">
    ${scoreBug(d, demo)}
    <div class="wrap">
      ${heroMove(d)}
      <div class="cols">
        <div class="col col-main">
          ${moves(d)}
          ${lineup(d)}
        </div>
        <aside class="col col-side" aria-label="Captain, chips and price timing">
          ${captain(d)}
          ${chips(d)}
          ${timing(d)}
        </aside>
      </div>
      ${league(d)}
      ${elite(d)}
      ${creatorRoom(d)}
      ${notes(d)}
    </div>
  </div>`;
}

/* Score bug: the sticky broadcast strip. Team, GW, live deadline clock, free transfers, bank, value.
   On phones the team line sits on its own row and scrolls away; the numbers row stays pinned. */
function scoreBug(d, demo) {
  const t = d.team || {}, gw = d.gameweek || {};
  const newSquad = !!(d.transfers?.initial || d.initial);
  const deadline = Date.parse(gw.deadline);
  return html`<div class="bug" role="region" aria-label="Your team this gameweek">
    <div class="wrap bug-row">
      <span class="bug-gw"><span class="bug-k">GW</span><b>${isNum(gw.id) ? gw.id : "–"}</b></span>
      <div class="bug-id">
        <span class="bug-name"><h2 id="results-title" tabindex="-1">${t.name || "Your team"}</h2>
          <span class="bug-mgr">${[t.manager, isNum(t.id) && !demo ? `Team ${t.id}` : null].filter(Boolean).join(" · ")}</span></span>
        ${demo ? html`<span class="bug-sample" title="An example team with made-up manager names">Sample</span>` : ""}
        <button type="button" class="bug-change" data-change-team aria-expanded="false" aria-controls="finder-panel">${demo ? "Use my team" : "Change team"}</button>
      </div>
      ${Number.isFinite(deadline) ? html`<span class="bug-cell deadline">
        <span class="bug-k">Deadline<span class="hide-sm"> in</span></span>
        <b class="clock" data-deadline="${new Date(deadline).toISOString()}">${countdown(deadline - Date.now())}</b>
        <span class="sr-only">${dateFmt.format(deadline)} your local time</span>
      </span>` : ""}
      ${newSquad ? "" : html`<span class="bug-cell"><span class="bug-k"><span class="hide-sm">Free transfers</span><span class="show-sm" aria-hidden="true">FT</span></span><b>${isNum(t.free_transfers) ? t.free_transfers : "–"}</b></span>
      <span class="bug-cell"><span class="bug-k">Bank</span><b>${money(t.bank)}</b></span>
      <span class="bug-cell bug-wide"><span class="bug-k">Squad value</span><b>${money(t.value)}</b></span>`}
      <span class="bug-cell bug-wide"><span class="bug-k">Total points</span><b>${int(t.total_points)}</b></span>
      ${Number.isFinite(deadline) ? html`<span class="bug-cell bug-wide bug-when"><span class="bug-k">Deadline, your time</span><span class="bug-local">${dateFmt.format(deadline)}</span></span>` : ""}
    </div>
  </div>
  ${demo ? html`<p class="sample-strip"><span class="wrap">This is an example team with made-up names for managers. Use “Use my team” to get your own advice.</span></p>` : ""}`;
}

// How many hits the plan takes. Hits apply to the plan, not to a particular move.
const planHits = (tr) => (tr && isNum(tr.hits) && tr.hits > 0 ? tr.hits : 0);

// A plain verdict for a transfer's gain over the horizon. Only talks about hits when the plan
// takes one; otherwise the move uses a free transfer and the whole gain counts.
function verdict(gain, hits) {
  if (!isNum(gain)) return "";
  if (gain <= 0) return "Loses expected points over this period.";
  if (hits > 0) return gain > 4 ? `Pays back a −4 hit on its own, netting +${f1(gain - 4)}.` : "Wouldn't pay back a −4 hit on its own.";
  return gain >= 10 ? "A big upgrade, and it uses a free transfer." : "Uses a free transfer, so it's pure gain.";
}

const bestMove = (list) => list.filter((m) => m && m.out && m.in && isNum(m.gain)).reduce((a, b) => (!a || b.gain > a.gain ? b : a), null);

/* Hero lower-third: the one sentence and the one number. */
function heroMove(d) {
  const tr = d.transfers || {};
  const hz = horizonLabel(d.horizon);
  const L = d.lineup, C = L && L.current;
  const list = Array.isArray(tr.moves) ? tr.moves : [];
  const sheetGain = L && C && isNum(L.expected_points) && isNum(C.expected_points) ? L.expected_points - C.expected_points : null;
  const chipNow = d.chip_now ? (d.chips || []).find((c) => c.play_now && (d.chip_now_code ? c.chip === d.chip_now_code : c.name === d.chip_now)) : null;
  const best = !tr.initial ? bestMove(list) : null;
  let tab = "Your best move", head, num = null, unit = "", sub = "", signedNum = true;
  if (tr.initial) {
    tab = "New squad"; signedNum = false;
    head = html`Pick your 15-player squad`;
    if (isNum(L?.expected_points)) { num = L.expected_points; unit = "pts expected this gameweek"; }
    sub = tr.headline ? md(tr.headline) : "";
  } else if (best) {
    head = html`<span class="hm-side out">${shirt(best.out.team, 44)}<span><span class="hm-verb">Sell</span> ${best.out.name}</span></span>
      <span class="hm-arrow" aria-hidden="true">→</span>
      <span class="hm-side in">${shirt(best.in.team, 44)}<span><span class="hm-verb">buy</span> ${best.in.name}</span></span>`;
    num = best.gain; unit = `pts over ${hz}`;
    sub = html`${verdict(best.gain, planHits(tr))}${list.length > 1 ? ` The biggest of the ${list.length} moves we suggest.` : ""}`;
  } else {
    tab = "Your move"; signedNum = false;
    head = html`Keep your team. Roll the free transfer.`;
    if (isNum(L?.expected_points)) { num = L.expected_points; unit = "pts expected this gameweek"; }
    sub = "No transfer adds enough this week. Saving it gives you two next week.";
  }
  const showSheetGain = sheetGain !== null && sheetGain > 0.05 && list.length > 0;
  return html`<section class="hero-move" aria-label="${tab}">
    <div class="lt">
      <span class="lt-tab">${tab}</span>
      <div class="lt-body">
        <p class="hm-head">${head}</p>
        ${num !== null ? html`<p class="hm-num"><b class="${num < 0 ? "neg" : ""}" data-count="${num.toFixed(1)}" data-sign="${signedNum ? 1 : 0}">${signedNum ? signed(num) : f1(num)}</b><span>${unit}</span></p>` : ""}
        ${sub ? html`<p class="hm-sub">${sub}</p>` : ""}
        ${showSheetGain ? html`<p class="hm-side-stat">${list.length > 1 ? `All ${list.length} moves` : "Our changes"} together add <b>+${f1(sheetGain)}</b> expected points this gameweek alone.</p>` : ""}
      </div>
    </div>
    ${d.chip_now ? html`<div class="chip-banner" role="note">
      <span class="state play">Chip</span>
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
  const badges = [showNews ? html`<span class="tag ${a.kind}">${a.text}</span>` : "", priceBadge(p), ...signalBadges(p, isOut)];
  return html`<div class="side ${dir}">
    ${shirt(p.team, 40, false)}
    <div class="side-txt">
      <span class="pname"><span class="dir">${isOut ? "Sell" : "Buy"}</span> ${p.name}</span>
      <span class="pmeta">${[p.team, p.pos].filter(Boolean).join(" · ")}</span>
      <span class="pmeta price">${capitalise(price)}</span>
      ${badges.some(Boolean) ? html`<div class="badges">${badges}</div>` : ""}
    </div>
  </div>`;
}

function moveWhy(m, id) {
  const o = m.out, n = m.in;
  const items = [];
  if (o && m.why_out) items.push(html`<div class="why-row out"><dt>Why sell ${o.name}</dt><dd>${md(capitalise(m.why_out))}</dd></div>`);
  if (n && m.why_in) items.push(html`<div class="why-row in"><dt>Why buy ${n.name}</dt><dd>${md(m.why_in)}</dd></div>`);
  const srcs = [o, n].filter((p) => p && p.team_news && safeUrl(p.team_news_source));
  if (!items.length && !srcs.length) return null;
  return html`<div class="why-panel" id="${id}" hidden>
    <dl class="why-list">${items}</dl>
    ${srcs.map((p) => html`<a class="src" href="${safeUrl(p.team_news_source)}" target="_blank" rel="noopener noreferrer">Team news source: ${p.name}</a>`)}
  </div>`;
}

// Bullet bar: gain against zero. The −4 marker is drawn only when the plan takes a hit.
function bullet(gain, scaleMax, showHit) {
  if (!isNum(gain)) return "";
  const lo = Math.min(0, gain), span = scaleMax - lo;
  const x = (v) => `${((v - lo) / span) * 100}%`;
  const from = Math.min(0, gain), to = Math.max(0, gain);
  return html`<div class="bullet ${showHit ? "with-hit" : ""}" role="img" aria-label="${signed(gain)} expected points${showHit ? ". A −4 hit needs a gain of more than 4 to pay off" : ""}.">
    <span class="b-track"></span>
    <span class="b-fill ${gain < 0 ? "neg" : ""}" style="left:${x(from)};width:${((to - from) / span) * 100}%"></span>
    <span class="b-zero" style="left:${x(0)}"></span>
    ${showHit ? html`<span class="b-hit" style="left:${x(4)}"><span>−4 hit</span></span>` : ""}
  </div>`;
}

const LEGEND = html`<ul class="legend" aria-label="Colour key">
  <li><span class="sw gain"></span>Gain, buy</li>
  <li><span class="sw bad"></span>Sell, loss, injured</li>
  <li><span class="sw warn"></span>Price change, doubtful</li>
  <li><span class="sw info"></span>Odds, elite managers</li>
</ul>`;

function moves(d) {
  const tr = d.transfers;
  if (!tr) return "";
  const hz = horizonLabel(d.horizon);
  if (tr.initial) {
    const squad = Array.isArray(tr.squad) ? tr.squad : [];
    return html`<section class="sec" aria-labelledby="moves-h">
      ${secHead("moves-h", "Pick your squad", tr.headline ? html`<p class="lede">${md(tr.headline)}</p>` : "")}
      <div class="squad-groups">
        ${POS.map((pos) => {
          const ps = squad.filter((p) => p.pos === pos);
          if (!ps.length) return "";
          return html`<div><h3 class="sub">${POS_LABEL[pos]}</h3><table class="tbl">
            <thead><tr><th scope="col">Player</th><th class="n" scope="col">Price</th><th class="n" scope="col">xP ${hz}</th></tr></thead>
            <tbody>${ps.map((p) => html`<tr>
              <td><span class="pcell">${shirt(p.team, 24)}<span><span class="pname">${p.name}</span> <span class="pmeta">${p.team} · next ${p.next_fixture || "–"}</span>
                <span class="badges">${newsBadge(p)}${priceBadge(p)}</span></span></span></td>
              <td class="n">${money(p.price)}</td><td class="n"><b>${f1(p.xp_horizon)}</b></td>
            </tr>`)}</tbody></table></div>`;
        })}
      </div>
      ${isNum(tr.bank_after) ? html`<p class="foot-note">Money left after buying this squad: <strong>${money(tr.bank_after)}</strong></p>` : ""}
    </section>`;
  }
  const list = Array.isArray(tr.moves) ? tr.moves : [];
  const hits = planHits(tr);
  const top = Math.max(10, ...list.map((m) => (isNum(m.gain) ? m.gain : 0)));
  const scaleMax = Math.ceil(top / 5) * 5;
  const hitLine = hits > 0
    ? ` This plan takes ${plural(hits, "hit", "hits")} (−${isNum(tr.hit_cost) ? tr.hit_cost : hits * 4} points), so each move is checked against the 4-point cost.`
    : list.length ? " All of them use free transfers, so there are no points deducted." : "";
  return html`<section class="sec" aria-labelledby="moves-h">
    ${secHead("moves-h", "Your transfers", html`<p class="lede">${tr.headline ? md(tr.headline) : ""} Gains are expected points over ${hz}.${hitLine}</p>`)}
    ${list.length ? html`${LEGEND}<ol class="moves">${list.map((m, i) => {
      const why = moveWhy(m, `why-${i + 1}`);
      return html`<li class="move">
      <span class="move-n" aria-hidden="true">${i + 1}</span>
      <div class="move-main">
        <h3 class="sr-only">Transfer ${i + 1}</h3>
        ${m.out ? sidePlayer(m.out, "out", m.why_out) : ""}
        <span class="arrow" aria-hidden="true">→</span>
        ${m.in ? sidePlayer(m.in, "in", m.why_in) : ""}
        ${isNum(m.gain) ? html`<div class="move-score">
          <p class="move-gain ${m.gain < 0 ? "neg" : ""}"><b>${signed(m.gain)}</b><span>pts</span></p>
          ${bullet(m.gain, scaleMax, hits > 0)}
          <p class="verdict">${verdict(m.gain, hits)}</p>
        </div>` : ""}
        ${why ? html`<button type="button" class="why-btn" data-why aria-expanded="false" aria-controls="why-${i + 1}">Why?</button>` : ""}
      </div>
      ${why || ""}
    </li>`;
    })}</ol>` : html`<p class="empty-line">No transfers needed this week. Roll your free transfer and you'll have more to use next week.</p>`}
    ${tr.hit_note ? html`<p class="callout">${md(tr.hit_note)}</p>` : ""}
    ${hits > 0 && !tr.hit_note ? html`<p class="callout">This plan costs ${tr.hit_cost} points in hits (${plural(hits, "extra transfer", "extra transfers")}).</p>` : ""}
    ${isNum(tr.bank_after) && list.length ? html`<p class="foot-note">Money in the bank after these moves: <strong>${money(tr.bank_after)}</strong></p>` : ""}
  </section>`;
}

// Phone-only "Show all" toggles for long tables and the creator cards.
document.addEventListener("click", (e) => {
  const btn = e.target.closest?.("[data-reveal]");
  if (!btn) return;
  const target = document.getElementById(btn.getAttribute("aria-controls"));
  if (!target) return;
  const open = !target.classList.contains("open");
  target.classList.toggle("open", open);
  btn.setAttribute("aria-expanded", String(open));
  btn.textContent = open ? btn.dataset.less : btn.dataset.more;
});

// "Why?" toggles on transfers.
document.addEventListener("click", (e) => {
  const btn = e.target.closest?.("[data-why]");
  if (!btn) return;
  const panel = document.getElementById(btn.getAttribute("aria-controls"));
  if (!panel) return;
  const open = btn.getAttribute("aria-expanded") !== "true";
  btn.setAttribute("aria-expanded", String(open));
  panel.hidden = !open;
});

// "Change team" in the score bug opens the finder above the results.
document.addEventListener("click", (e) => {
  const btn = e.target.closest?.("[data-change-team]");
  if (!btn) return;
  const open = !document.body.classList.contains("finder-open");
  document.body.classList.toggle("finder-open", open);
  $$("[data-change-team]").forEach((b) => b.setAttribute("aria-expanded", String(open)));
  if (open) {
    window.scrollTo({ top: 0, behavior: reduceMotion() ? "auto" : "smooth" });
    teamIn.focus({ preventScroll: true });
  }
});

/* Landing glimpse: a small broadcast composition drawn from the example data with the same helpers. */
function glimpse(d) {
  const tr = d.transfers || {}, L = d.lineup || {}, c = d.captain || {};
  const best = bestMove(Array.isArray(tr.moves) ? tr.moves : []);
  const xi = Array.isArray(L.xi) ? L.xi : [];
  const front = xi.filter((p) => p.pos === "FWD").slice(0, 3);
  const mult = isNum(c.multiplier) ? c.multiplier : 2;
  const chip = (Array.isArray(d.chips) ? d.chips : []).find((x) => x.play_now) || (d.chips || [])[0];
  return html`<figcaption class="glimpse-cap">What you'll get <span class="glimpse-gw">Example team${isNum(d.gameweek?.id) ? `, gameweek ${d.gameweek.id}` : ""}</span></figcaption>
    <div class="glimpse-pitch" aria-hidden="true"></div>
    ${front.length ? html`<div class="g-form" aria-hidden="true">${front.map((p) => html`<span class="g-pl">${shirt(p.team, 46)}<span class="g-nm">${p.name}</span><span class="g-xp1">${f1(p.xp_next)}</span>${p.id === L.captain_id ? html`<span class="g-c">C</span>` : ""}</span>`)}</div>` : ""}
    ${best ? html`<div class="g-lt">
      <span class="g-tab">Your best move</span>
      <div class="g-body">
        <p class="g-swap"><span class="g-side">${shirt(best.out.team, 30, false)}<span><span class="g-sell">Sell</span> ${best.out.name}</span></span><span class="g-arrow" aria-hidden="true">→</span><span class="g-side">${shirt(best.in.team, 30, false)}<span><span class="g-buy">buy</span> ${best.in.name}</span></span></p>
        <p class="g-num"><span class="g-big">${signed(best.gain)}</span> <span class="g-unit">pts over ${horizonLabel(d.horizon)}</span></p>
        ${best.why_out ? html`<p class="g-why">Why sell: ${md(capitalise(best.why_out))}</p>` : ""}
      </div>
    </div>` : ""}
    ${c.captain ? html`<div class="g-row">
      <span class="g-arm" aria-hidden="true">C</span>${shirt(c.captain.team, 28, false)}
      <span class="g-name">${c.captain.name} <span class="g-fx">${c.captain.next_fixture ? `v ${c.captain.next_fixture}` : ""}</span></span>
      <span class="g-xp">${f1(c.captain.xp_next * mult)} <small>pts as captain</small></span>
    </div>` : ""}
    ${chip ? html`<div class="g-row">
      <span class="g-chip">${chip.play_now ? "Play now" : "Keep"}</span>
      <span class="g-name">${chip.name || chip.chip} <span class="g-fx">${isNum(chip.expires_gw) ? `expires after GW${chip.expires_gw}` : ""}</span></span>
    </div>` : ""}`;
}

async function loadGlimpse() {
  const fig = $("#glimpse");
  if (!fig) return;
  try {
    const res = await fetch("/static/sample_advice.json");
    if (!res.ok) throw new Error(String(res.status));
    setHTML(fig, glimpse(await res.json()));
    fig.hidden = false;
  } catch { fig.hidden = true; }
}

function captain(d) {
  const c = d.captain;
  if (!c || !c.captain) return "";
  const mult = isNum(c.multiplier) ? c.multiplier : 2;
  const p = c.captain, v = c.vice;
  const opts = Array.isArray(c.options) ? c.options : [];
  const maxXp = Math.max(1, ...opts.map((o) => (isNum(o.xp_next) ? o.xp_next : 0)));
  return html`<section class="sec card cap-card" aria-labelledby="cap-h">
    ${secHead("cap-h", "Captain", html`<p class="aside">Your captain scores ${mult === 3 ? "triple" : "double"} points. The vice takes over if the captain doesn't play.</p>`)}
    <div class="cap main">
      <span class="armband" aria-hidden="true">C</span>
      ${shirt(p.team, 56)}
      <div class="info">
        <span class="role">${mult === 3 ? "Triple captain" : "Captain"}</span>
        <span class="pname">${p.name}</span>
        <span class="pmeta">${[p.team, p.next_fixture ? `v ${p.next_fixture}` : null].filter(Boolean).join(" · ")}</span>
      </div>
      <div class="xp"><b data-count="${isNum(p.xp_next) ? (p.xp_next * mult).toFixed(1) : ""}">${f1(p.xp_next * mult)}</b><span>${f1(p.xp_next)} xP × ${mult}</span></div>
    </div>
    ${v ? html`<div class="cap vice">
      <span class="armband v" aria-hidden="true">V</span>
      ${shirt(v.team, 32, false)}
      <div class="info">
        <span class="role">Vice-captain</span>
        <span class="pname">${v.name}</span>
        <span class="pmeta">${[v.team, v.next_fixture ? `v ${v.next_fixture}` : null].filter(Boolean).join(" · ")}</span>
      </div>
      <div class="xp"><b>${f1(v.xp_next)}</b><span>xP</span></div>
    </div>` : ""}
    ${c.note ? html`<p class="note">${md(c.note)}</p>` : ""}
    ${opts.length ? html`<table class="tbl tbl-compact">
      <caption>Top options this week</caption>
      <thead><tr><th scope="col" class="n">#</th><th scope="col">Player</th><th scope="col" class="n">xP</th></tr></thead>
      <tbody>${opts.map((o, i) => html`<tr>
        <td class="n rank">${i + 1}</td>
        <td><span class="pcell">${shirt(o.team, 22, false)}<span><span class="pname">${o.name}</span> <span class="pmeta">${o.team} · v ${o.next_fixture || "–"}</span></span></span></td>
        <td class="n"><span class="numbar"><span class="mini" aria-hidden="true"><span style="width:${Math.round(clamp01(o.xp_next / maxXp) * 100)}%"></span></span><b>${f1(o.xp_next)}</b></span></td>
      </tr>`)}</tbody>
    </table>` : ""}
  </section>`;
}

function pitchChip(p, { capId, viceId, order, fresh } = {}) {
  const a = availability(p);
  const cv = p.id === capId ? "C" : p.id === viceId ? "V" : null;
  const isNew = fresh && fresh.has(p.id);
  const label = [p.name, p.team, p.next_fixture ? `v ${p.next_fixture}` : null, `${f1(p.xp_next)} expected points`,
    cv === "C" ? "captain" : cv === "V" ? "vice-captain" : null, isNew ? "suggested signing" : null,
    a ? `Team news: ${a.text}` : null].filter(Boolean).join(", ");
  return html`<li class="pl ${isNew ? "is-new" : ""}" data-pid="${p.id}" title="${a ? a.text : ""}">
    <span class="sr-only">${order ? `Bench ${order}: ` : ""}${label}</span>
    <span class="pl-kit" aria-hidden="true">
      ${shirt(p.team, 52)}
      ${cv ? html`<span class="cv ${cv === "V" ? "v" : ""}">${cv}</span>` : ""}
      ${a ? html`<span class="flag ${a.kind}">!</span>` : ""}
      ${isNew ? html`<span class="newtag">In</span>` : ""}
    </span>
    <span class="nm" aria-hidden="true">${p.name}</span>
    <span class="pl-foot" aria-hidden="true"><span class="fx">${p.next_fixture || "–"}</span><span class="xpn">${f1(p.xp_next)}</span></span>
    ${order ? html`<span class="order" aria-hidden="true">${order}</span>` : ""}
  </li>`;
}

// One team sheet: the pitch, the bench and any team news for it.
function sheet(L, { fresh, benchBoost } = {}) {
  const opts = { capId: L.captain_id, viceId: L.vice_id, fresh };
  const bench = Array.isArray(L.bench) ? L.bench : [];
  const flagged = [...L.xi, ...bench].filter((p) => availability(p));
  return html`<div class="pitch">
      <div class="pitch-field">
        <div class="pitch-lines" aria-hidden="true"></div>
        ${POS.map((pos) => {
          const row = L.xi.filter((p) => p.pos === pos);
          return row.length ? html`<ul class="prow" aria-label="${POS_LABEL[pos]}">${row.map((p) => pitchChip(p, opts))}</ul>` : "";
        })}
      </div>
      ${bench.length ? html`<div class="bench">
        <h3 class="bench-h">${benchBoost ? "Bench (Bench Boost on: these score too)" : "Bench, in order"}</h3>
        <ul class="prow" aria-label="Bench in order">${bench.map((p, i) => pitchChip(p, { ...opts, order: i + 1 }))}</ul>
      </div>` : ""}
    </div>
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
      ${secHead("xi-h", "Your starting XI",
        isNum(L.expected_points) ? html`<p class="aside">Expected points this week <b class="num">${f1(L.expected_points)}</b></p>` : "")}
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
    ${secHead("xi-h", "Your starting XI",
      gain !== null && gain > 0.05 ? html`<p class="aside">Our moves add <b class="num gain-ink">+${f1(gain)}</b> expected points this week.</p>` : "")}
    <div class="seg" role="group" aria-label="Which team sheet to show">
      ${tab("now", "Your team now", C.expected_points)}
      ${tab("after", "After our moves", L.expected_points)}
    </div>
    <p class="hint sheet-note" data-sheet-note="now" ${view === "now" ? "" : "hidden"}>The XI, bench and captain you last saved in FPL. It's what scores if you change nothing.</p>
    <p class="hint sheet-note" data-sheet-note="after" ${view === "after" ? "" : "hidden"}>Your team with the transfers, captain and chips suggested above. Players marked <span class="newtag inline">In</span> are the suggested signings.</p>
    <div class="sheet-view" data-sheet-view="now" ${view === "now" ? "" : "hidden"}>${sheet(C)}</div>
    <div class="sheet-view" data-sheet-view="after" ${view === "after" ? "" : "hidden"}>${after}</div>
  </section>`;
}

// Toggle Now / After. Shirts that exist in both views slide from their old spot (FLIP);
// new signings fade in. No motion with reduced-motion.
document.addEventListener("click", (e) => {
  const btn = e.target.closest?.("[data-sheet]");
  if (!btn) return;
  const sec = btn.closest("section");
  const next = btn.dataset.sheet;
  if (next === state.sheet) return;
  const oldView = sec.querySelector(`[data-sheet-view="${state.sheet}"]`);
  const first = new Map();
  if (oldView && !reduceMotion()) $$("[data-pid]", oldView).forEach((el) => first.set(el.dataset.pid, el.getBoundingClientRect()));
  state.sheet = next;
  sec.querySelectorAll("[data-sheet]").forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
  sec.querySelectorAll("[data-sheet-view],[data-sheet-note]").forEach((el) => {
    el.hidden = (el.dataset.sheetView || el.dataset.sheetNote) !== state.sheet;
  });
  if (!first.size) return;
  const newView = sec.querySelector(`[data-sheet-view="${state.sheet}"]`);
  $$("[data-pid]", newView).forEach((el) => {
    const was = first.get(el.dataset.pid);
    const now = el.getBoundingClientRect();
    if (was) {
      const dx = was.left - now.left, dy = was.top - now.top;
      if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return;
      el.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }],
        { duration: 420, easing: "cubic-bezier(.2,.7,.2,1)" });
    } else {
      el.animate([{ opacity: 0, transform: "translateY(10px) scale(.9)" }, { opacity: 1, transform: "none" }],
        { duration: 360, delay: 120, easing: "ease-out", fill: "backwards" });
    }
  });
});

function chips(d) {
  const list = Array.isArray(d.chips) ? d.chips : [];
  if (!list.length) return "";
  return html`<section class="sec" aria-labelledby="chips-h">
    ${secHead("chips-h", "Chips")}
    <ul class="chips">${list.map((c) => html`<li class="chiprow ${c.play_now ? "now" : ""}">
      <div class="chip-head">
        <span class="chip-name">${c.name || c.chip}</span>
        ${c.play_now ? html`<span class="state play">Play now</span>` : html`<span class="state keep">Keep</span>`}
        ${isNum(c.expires_gw) ? html`<span class="exp">Expires after GW${c.expires_gw}</span>` : ""}
      </div>
      ${c.reason ? html`<details class="why" ${c.play_now ? "open" : ""}><summary>${c.play_now ? "Why now?" : "Why wait?"}</summary><p>${md(c.reason)}</p></details>` : ""}
    </li>`)}</ul>
  </section>`;
}

function timing(d) {
  const list = Array.isArray(d.timing) ? d.timing.filter(Boolean) : [];
  if (!list.length) return "";
  return html`<section class="sec" aria-labelledby="timing-h">
    ${secHead("timing-h", "When to make your moves")}
    <ul class="ticks warn">${list.map((t) => html`<li><span class="tick-ic" aria-hidden="true">▲</span><span>${md(t)}</span></li>`)}</ul>
  </section>`;
}

function ownTable(caption, players, empty, partial) {
  if (!Array.isArray(players) || !players.length) return html`<div><h3 class="sub">${caption}</h3><p class="empty-line">${empty}</p></div>`;
  const id = nextId("own");
  return html`<div><table class="tbl ${players.length > 5 ? "clip5" : ""}" id="${id}">
    <caption>${caption}</caption>
    <thead><tr><th scope="col">Player</th><th scope="col" class="n">${partial ? "Tracked rivals who own" : "Rivals who own"}</th></tr></thead>
    <tbody>${players.map((p) => html`<tr>
      <td><span class="pcell">${shirt(p.team, 22, false)}<span><span class="pname">${p.name}</span> <span class="pmeta">${[p.team, p.pos].filter(Boolean).join(" · ")}</span></span></span></td>
      <td class="n"><span class="numbar"><span class="mini" aria-hidden="true"><span style="width:${Math.round(clamp01(p.ownership) * 100)}%"></span></span><b>${pct(p.ownership)}</b></span></td>
    </tr>`)}</tbody>
  </table>${players.length > 5 ? moreBtn(id, players.length) : ""}</div>`;
}

function league(d) {
  const L = d.league;
  if (!L) return "";
  const me = d.team?.total_points;
  const table = Array.isArray(L.table) ? L.table : [];
  const rivalChips = (Array.isArray(L.rival_chips) ? L.rival_chips : []).filter((r) => r && Array.isArray(r.chips));
  const chipsBy = new Map(rivalChips.map((r) => [r.manager, r.chips]));
  const inTable = new Set(table.map((r) => r.manager));
  const leftover = rivalChips.filter((r) => !inTable.has(r.manager));
  const leader = table.find((r) => r.rank === 1) || table[0];
  const leaderPts = leader && isNum(leader.total) ? leader.total : null;
  const hasChips = rivalChips.length > 0;
  const rows = [];
  table.forEach((r, i) => {
    if (i > 0 && isNum(r.rank) && isNum(table[i - 1].rank) && r.rank - table[i - 1].rank > 1) {
      rows.push(html`<tr class="gap" aria-hidden="true"><td colspan="${hasChips ? 6 : 5}">…</td></tr>`);
    }
    const behind = leaderPts !== null && isNum(r.total) ? leaderPts - r.total : null;
    const cs = chipsBy.get(r.manager);
    rows.push(html`<tr class="${r.is_me ? "me" : ""}">
      <td class="n rank">${r.rank}</td>
      <td class="who"><span class="mgr">${r.manager}</span>${r.is_me ? html` <span class="you">You</span>` : ""} <span class="team">${r.team_name}</span></td>
      <td class="n">${int(r.event_total)}</td>
      <td class="n"><b>${int(r.total)}</b></td>
      <td class="n behind ${behind === 0 ? "lead" : ""}">${behind === null ? "–" : behind === 0 ? "Leader" : `−${int(behind)}`}</td>
      ${hasChips ? html`<td class="chips-cell">${r.is_me || !cs ? "" : cs.length ? cs.map((c) => html`<span class="pill">${c}</span>`) : html`<span class="muted small">none left</span>`}</td>` : ""}
    </tr>`);
  });
  const lead = isNum(L.gap_to_leader) && L.gap_to_leader <= 0;
  const second = table.find((r) => !r.is_me);
  const myRow = table.find((r) => r.is_me);
  const myTotal = myRow && isNum(myRow.total) ? myRow.total : me;
  const leadBy = lead && second && isNum(myTotal) && isNum(second.total) ? myTotal - second.total : null;
  const onPace = isNum(L.my_average) && isNum(L.required_per_gw) && L.my_average >= L.required_per_gw;
  const total = isNum(L.total_managers) && L.total_managers > 0 ? L.total_managers : null;
  const partial = total != null && isNum(L.tracked) && L.tracked < total;
  const paceMax = Math.max(L.required_per_gw || 0, L.my_average || 0, 1) * 1.15;
  const standId = nextId("standings");

  return html`<section class="sec sec-wide" aria-labelledby="league-h">
    ${secHead("league-h", L.name || "Your league",
      isNum(L.rank) ? html`<p class="lede">You're <strong>${ordinal(L.rank)}</strong>${total != null ? ` of ${int(total)}` : ""}${
        lead ? (isNum(leadBy) && leadBy > 0 ? `, ${plural(leadBy, "point", "points")} clear at the top.` : isNum(leadBy) ? ", level on points at the top." : ", top of the league.")
        : isNum(L.gap_to_leader) ? `, ${plural(L.gap_to_leader, "point", "points")} behind the leader.` : "."}</p>` : "")}
    <div class="league-grid">
      <div class="standings">
        <div class="table-wrap" tabindex="0" role="region" aria-label="League standings">
          <table class="tbl tbl-standings ${table.length > 5 ? "clip5" : ""}" id="${standId}">
            <caption class="sr-only">League standings</caption>
            <thead><tr><th class="n" scope="col">#</th><th scope="col">Manager</th><th class="n" scope="col">GW</th><th class="n" scope="col">Total</th><th class="n" scope="col">To 1st</th>${hasChips ? html`<th scope="col">Chips left</th>` : ""}</tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
        ${table.length > 5 ? moreBtn(standId, table.length) : ""}
        ${leftover.length ? html`<table class="tbl tbl-compact"><caption>Other rivals' chips still to play</caption>
          <tbody>${leftover.map((r) => html`<tr><th scope="row">${r.manager}</th><td class="chips-cell">${r.chips.length ? r.chips.map((c) => html`<span class="pill">${c}</span>`) : html`<span class="muted">none left</span>`}</td></tr>`)}</tbody></table>` : ""}
      </div>
      <div class="stack">
        ${isNum(L.target) ? html`<div class="target">
          <p class="target-head"><span>Points likely needed to win</span><b>${int(L.target)}</b></p>
          ${isNum(me) ? html`<div class="bar" role="img" aria-label="${int(me)} of ${int(L.target)} points so far"><span style="width:${Math.round(clamp01(me / L.target) * 100)}%"></span></div>
          <p class="small muted">${int(me)} so far${isNum(L.remaining_gws) ? ` · ${int(Math.max(0, L.target - me))} to go in ${plural(L.remaining_gws, "gameweek", "gameweeks")}` : ""}</p>` : ""}
          ${isNum(L.required_per_gw) ? html`<div class="pace">
            <div class="pace-row"><span>Needed per gameweek</span><span class="mini wide" aria-hidden="true"><span class="need" style="width:${Math.round(clamp01(L.required_per_gw / paceMax) * 100)}%"></span></span><b>${f1(L.required_per_gw)}</b></div>
            <div class="pace-row"><span>Your average</span><span class="mini wide" aria-hidden="true"><span class="${onPace ? "" : "short"}" style="width:${Math.round(clamp01((L.my_average || 0) / paceMax) * 100)}%"></span></span><b>${f1(L.my_average)}</b></div>
          </div>
          <p class="small">${onPace
            ? html`<span class="state play">On pace</span> Keep scoring like this and you reach the target.`
            : html`<span class="state warn">Behind pace</span> You need about ${f1(L.required_per_gw - L.my_average)} more points a week.`}</p>` : ""}
          ${L.last_season_best ? html`<p class="small muted">Best total last season among these managers: ${int(L.last_season_best.points)} (${L.last_season_best.manager}).</p>` : ""}
        </div>` : ""}
        ${L.strategy ? html`<div class="strategy"><h3 class="sub">Your strategy</h3><p class="small">${md(L.strategy)}</p></div>` : ""}
      </div>
    </div>
    <div class="grid2">
      ${ownTable("Popular with rivals, not in your team", L.threats, "None of your rivals' popular picks are missing from your team.", partial)}
      ${ownTable("Your differentials", L.differentials, "No differentials in your XI this week.", partial)}
    </div>
    ${partial ? html`<p class="small muted gap-top">Ownership is shown as a share of the rivals we track (top 10 and those near you), not the whole league.</p>` : ""}
  </section>`;
}

function eliteTable(caption, rows, kind, empty) {
  if (!rows?.length) return html`<div><h3 class="sub">${caption}</h3><p class="empty-line">${empty}</p></div>`;
  const maxN = Math.max(1, ...rows.map((p) => (kind === "in" ? p.elite_in : p.elite_out) || 0));
  const head = kind === "own" ? "Own" : kind === "in" ? "Bought by" : "Sold by";
  const id = nextId("elite");
  return html`<div><table class="tbl ${rows.length > 5 ? "clip5" : ""}" id="${id}">
    <caption>${caption}</caption>
    <thead><tr><th scope="col">Player</th><th scope="col" class="n">${head}</th></tr></thead>
    <tbody>${rows.map((p) => {
      const frac = kind === "own" ? clamp01(p.elite_own) : clamp01(((kind === "in" ? p.elite_in : p.elite_out) || 0) / maxN);
      const val = kind === "own" ? pct(p.elite_own) : kind === "in" ? `+${p.elite_in ?? 0}` : `−${p.elite_out ?? 0}`;
      return html`<tr>
        <td><span class="pcell">${shirt(p.team, 22, false)}<span><span class="pname">${p.name}</span> <span class="pmeta">${[p.team, p.pos, money(p.price)].filter(Boolean).join(" · ")}</span>
          ${p.in_your_team ? html` <span class="pill own">In your team</span>` : ""}</span></span></td>
        <td class="n"><span class="numbar ${kind === "out" ? "loss" : "sky"}"><span class="mini" aria-hidden="true"><span style="width:${Math.round(frac * 100)}%"></span></span><b>${val}</b></span>
          ${kind === "own" && isNum(p.elite_captain) && p.elite_captain > 0.05 ? html`<span class="subnum">${pct(p.elite_captain)} captain</span>` : ""}</td>
      </tr>`;
    })}</tbody>
  </table>${rows.length > 5 ? moreBtn(id, rows.length) : ""}</div>`;
}

function elite(d) {
  const E = d.elite;
  if (!E || !Array.isArray(E.most_owned) || !E.most_owned.length) return "";
  const seasons = Array.isArray(E.seasons) && E.seasons.length ? `${E.seasons[0]} to ${E.seasons[E.seasons.length - 1]}` : "the last three seasons";
  return html`<section class="sec sec-wide" aria-labelledby="elite-h">
    ${secHead("elite-h", "What the most consistent managers are doing",
      html`<p class="lede">The ${E.managers} managers with the best typical finish over ${seasons}${isNum(E.median_rank) ? html` (a typical finish of about <strong>${ordinal(Math.round(E.median_rank))}</strong> overall)` : ""}. Their teams are public only after each deadline, so this is from gameweek ${E.gameweek}.</p>`)}
    <div class="grid2">
      <div class="stack">${eliteTable("Most owned", E.most_owned, "own", "No data yet.")}</div>
      <div class="stack">
        ${eliteTable("Popular with them, missing from your team", E.missing, "own", "You already own all their favourites.")}
        ${eliteTable("Bought last gameweek", E.bought, "in", "No clear buys.")}
        ${eliteTable("Sold last gameweek", E.sold, "out", "No clear sales.")}
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
    ${shirt(p.team, 16, false)}${p.name}${p.team ? html` <span class="tm">${p.team}</span>` : ""}</span>`;
}

function chipGroup(label, items) {
  return items.length ? html`<div class="cgroup"><span class="clabel">${label}</span><span class="citems">${items}</span></div>` : "";
}

function shortDate(iso) {
  const t = new Date(String(iso || ""));
  return Number.isNaN(t.getTime()) ? "" : t.toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short" });
}

function initials(name) {
  const words = String(name || "").replace(/['’]/g, "").replace(/[^\p{L}\p{N}\s]/gu, " ").split(/\s+/).filter(Boolean);
  const pick = words.filter((w) => w.toUpperCase() !== "FPL");
  const src = pick.length ? pick : words;
  return (src.length > 1 ? src[0][0] + src[1][0] : (src[0] || "?").slice(0, 2)).toUpperCase();
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
      <span class="mono" aria-hidden="true">${initials(c.name)}</span>
      <div class="c-id">
        ${curl ? html`<a class="c-name" href="${curl}" target="_blank" rel="noopener noreferrer">${c.name}</a>` : html`<span class="c-name">${c.name}</span>`}
        ${v && v.published ? html`<span class="muted small">${shortDate(v.published)}</span>` : ""}
      </div>
      ${CHIP_LABEL[c.chip] ? html`<span class="tag info">Playing ${CHIP_LABEL[c.chip]}</span>` : ""}
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
        ${CHIP_LABEL[A.chip] ? html`<span class="tag info">${CHIP_LABEL[A.chip]}</span>` : ""}
        ${isNum(A.hit) && A.hit > 0 ? html`<span class="tag warn">−${A.hit} hit</span>` : ""}
      </div></div>` : ""}
  </li>`;
}

// Agreement bar: count of `of` creators, one segment each.
function segs(count, of, cls) {
  if (!isNum(count) || !isNum(of) || of <= 0 || of > 20) return "";
  return html`<span class="segs" aria-hidden="true">${Array.from({ length: of }, (_, i) => html`<span class="${i < count ? cls : ""}"></span>`)}</span>`;
}

function creatorRoom(d) {
  const R = d.creators;
  if (!R || !Array.isArray(R.creators) || !R.creators.length) return "";
  const agree = Array.isArray(R.agree) ? R.agree : [];
  const split = Array.isArray(R.split) ? R.split : [];
  const n = R.creators.length;
  const listId = nextId("creators");
  const enough = isNum(R.with_plan) && R.with_plan >= 2;
  const kindCls = (k) => (k === "sell" ? "bad" : k === "captain" ? "info" : "gain");
  const yours = (x) => (isRef(x.player) && x.player.in_your_team ? html` <span class="pill own">In your team</span>` : "");
  const agreeList = agree.length ? html`<ul class="consensus">${agree.map((a) => html`<li>
      <span class="cs-txt">${isRef(a.player) ? shirt(a.player.team, 20, false) : ""}<span>${a.text}${yours(a)}</span></span>
      <span class="cs-viz">${segs(a.count, a.of, kindCls(a.kind))}<b>${a.count}/${a.of}</b></span></li>`)}</ul>`
    : html`<p class="empty-line">No player has at least half of them behind it.</p>`;
  const splitBar = (x) => {
    if (x.kind === "captain" && Array.isArray(x.options)) {
      const tot = x.options.reduce((s, o) => s + (Array.isArray(o.who) ? o.who.length : 0), 0);
      if (!tot) return "";
      return html`<span class="splitbar" aria-hidden="true">${x.options.map((o, i) => html`<span class="s${i % 3}" style="flex:${Array.isArray(o.who) ? o.who.length : 0}">${isRef(o.player) ? o.player.name : ""}</span>`)}</span>`;
    }
    if (Array.isArray(x.buying) && Array.isArray(x.selling) && x.buying.length + x.selling.length) {
      return html`<span class="splitbar" aria-hidden="true"><span class="buy" style="flex:${x.buying.length}">Buy ${x.buying.length}</span><span class="sell" style="flex:${x.selling.length}">Sell ${x.selling.length}</span></span>`;
    }
    return "";
  };
  const splitList = split.length ? html`<ul class="consensus">${split.map((x) => html`<li>
      <span class="cs-txt">${isRef(x.player) ? shirt(x.player.team, 20, false) : ""}<span>${x.text}${yours(x)}</span></span>
      <span class="cs-viz">${splitBar(x)}</span></li>`)}</ul>`
    : html`<p class="empty-line">No clear disagreements.</p>`;
  return html`<section class="sec sec-wide" aria-labelledby="creators-h">
    ${secHead("creators-h", "What the big FPL YouTubers are planning",
      html`<p class="lede">From each creator's latest gameweek ${R.gameweek} plan video${R.read_by_llm ? ", read from its title and description" : ""}. ${plural(isNum(R.with_video) ? R.with_video : 0, "of " + n + " has", "of " + n + " have")} posted one so far.</p>`)}
    ${R.read_by_llm && enough ? html`<div class="grid2">
      <div><h3 class="sub">They agree</h3>${agreeList}</div>
      <div><h3 class="sub">They're split</h3>${splitList}</div>
    </div>` : ""}
    <h3 class="sub">Creator by creator</h3>
    <p class="small muted legend-line">Solid chips are decided moves; dashed chips are players they are still thinking about.</p>
    <button type="button" class="more-btn sm-only" data-reveal aria-controls="${listId}" aria-expanded="false" data-more="Show each creator's plan (${n})" data-less="Hide the creators' plans">Show each creator's plan (${n})</button>
    <ul class="creator-list collapse-sm" id="${listId}">${R.creators.map((c) => creatorRow(c, R))}</ul>
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
  return html`<section class="sec sec-wide small-print" aria-labelledby="notes-h">
    ${secHead("notes-h", "Good to know")}
    ${list.length ? html`<ul class="ticks">${list.map((x) => html`<li><span class="tick-ic" aria-hidden="true">i</span><span>${md(x)}</span></li>`)}</ul>` : ""}
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
  if (params.get("demo") !== "1") loadGlimpse();
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
