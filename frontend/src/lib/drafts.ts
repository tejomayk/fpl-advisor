// What-if transfer drafts: kept in this browser only (localStorage), one per team per gameweek,
// and shareable as a link that holds player IDs only. Nothing is stored on the server.
// Pure functions with the storage passed in, so they can be tested without a browser
// (frontend/tests/drafts.test.ts).

/** One transfer: [player sold, player bought], as FPL player IDs. */
export type Pair = [number, number];

export interface Draft {
  team: number;
  gw: number;
  /** The gameweek's deadline (ISO). The draft is dropped once it passes. */
  deadline: string;
  moves: Pair[];
  saved: string;
}

/** The part of Storage the drafts use (localStorage in the browser, a fake in tests). */
export interface KV {
  getItem(k: string): string | null;
  setItem(k: string, v: string): void;
  removeItem(k: string): void;
  key(i: number): string | null;
  readonly length: number;
}

const PREFIX = "fpl-advisor.draft.";
export const MAX_MOVES = 15;
export const HIT_COST = 4;

export const draftKey = (team: number, gw: number) => `${PREFIX}${team}.${gw}`;

/** localStorage, or null when the browser blocks it. */
export function browserStore(): KV | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

const below = (v: unknown, max: number): v is number => typeof v === "number" && Number.isInteger(v) && v > 0 && v < max;
/** FPL player IDs (a few hundred a season) and gameweeks. */
const isId = (v: unknown): v is number => below(v, 1e6);
/** FPL team IDs run to 8 digits. */
const isTeam = (v: unknown): v is number => below(v, 1e8);

function cleanMoves(v: unknown): Pair[] | null {
  if (!Array.isArray(v) || v.length > MAX_MOVES) return null;
  const out: Pair[] = [];
  for (const p of v) {
    if (!Array.isArray(p) || p.length !== 2 || !isId(p[0]) || !isId(p[1])) return null;
    out.push([p[0], p[1]]);
  }
  return out;
}

function parse(raw: string | null): Draft | null {
  if (!raw) return null;
  try {
    const d = JSON.parse(raw) as Partial<Draft>;
    const moves = cleanMoves(d.moves);
    if (!isTeam(d.team) || !isId(d.gw) || typeof d.deadline !== "string" || !moves) return null;
    return { team: d.team, gw: d.gw, deadline: d.deadline, moves, saved: String(d.saved || "") };
  } catch {
    return null;
  }
}

const passed = (deadline: string, now: number) => {
  const t = Date.parse(deadline);
  return Number.isFinite(t) && now >= t;
};

export function saveDraft(s: KV | null, d: Omit<Draft, "saved">, now = Date.now()): boolean {
  if (!s) return false;
  try {
    s.setItem(draftKey(d.team, d.gw), JSON.stringify({ ...d, saved: new Date(now).toISOString() }));
    return true;
  } catch {
    return false;
  }
}

export function clearDraft(s: KV | null, team: number, gw: number) {
  try {
    s?.removeItem(draftKey(team, gw));
  } catch {
    /* storage blocked */
  }
}

/** The saved draft for this team and gameweek, or null. A draft whose deadline has passed is
    removed and not returned. */
export function readDraft(s: KV | null, team: number, gw: number, now = Date.now()): Draft | null {
  if (!s) return null;
  let d: Draft | null;
  try {
    d = parse(s.getItem(draftKey(team, gw)));
  } catch {
    return null;
  }
  if (!d || d.team !== team || d.gw !== gw) return null;
  if (passed(d.deadline, now)) {
    clearDraft(s, team, gw);
    return null;
  }
  return d;
}

/** Remove this team's drafts for gameweeks whose deadline has passed, and return them, so the
    page can check them against the squad FPL now shows. */
export function takeOldDrafts(s: KV | null, team: number, gw: number, now = Date.now()): Draft[] {
  if (!s) return [];
  const out: Draft[] = [];
  const keys: string[] = [];
  try {
    for (let i = 0; i < s.length; i++) {
      const k = s.key(i);
      if (k && k.startsWith(`${PREFIX}${team}.`)) keys.push(k);
    }
    for (const k of keys) {
      const d = parse(s.getItem(k));
      const old = !d || d.gw < gw || (d.gw === gw && passed(d.deadline, now));
      if (!old) continue;
      s.removeItem(k);
      if (d) out.push(d);
    }
  } catch {
    /* storage blocked */
  }
  return out;
}

/** True when the squad has every player the draft bought and none it sold. */
export function draftMatchesSquad(d: Pick<Draft, "moves">, squad: number[]): boolean {
  const have = new Set(squad);
  return d.moves.every(([o, i]) => have.has(i) && !have.has(o));
}

/** gw 12, [[301, 55], [7, 200]] -> "12_301-55_7-200" (player IDs only, safe in a URL). */
export function encodeDraft(gw: number, moves: Pair[]): string {
  return [gw, ...moves.map(([o, i]) => `${o}-${i}`)].join("_");
}

export function decodeDraft(s: string | null | undefined): { gw: number; moves: Pair[] } | null {
  if (!s || s.length > 200) return null;
  const [g, ...rest] = s.split("_");
  const gw = Number(g);
  if (!/^\d{1,2}$/.test(g) || !isId(gw)) return null;
  const moves: Pair[] = [];
  for (const part of rest) {
    const m = part.match(/^(\d{1,6})-(\d{1,6})$/);
    if (!m) return null;
    moves.push([Number(m[1]), Number(m[2])]);
  }
  const clean = cleanMoves(moves);
  return clean && clean.length ? { gw, moves: clean } : null;
}

/** The `moves` query parameter for /api/whatif: "301-55,7-200". */
export const movesParam = (moves: Pair[]) => moves.map(([o, i]) => `${o}-${i}`).join(",");

export interface Priced {
  id: number;
  name?: string;
  team?: string;
  pos?: string;
  price?: number;
  sell_price?: number;
}

/** Money and hits for a draft, worked out before it is sent: what the editor shows. */
export function draftCost(
  moves: Pair[],
  squad: Priced[],
  players: Map<number, Priced>,
  bank: number,
  freeTransfers: number,
) {
  const mine = new Map(squad.map((p) => [p.id, p]));
  let income = 0;
  let cost = 0;
  for (const [o, i] of moves) {
    const s = mine.get(o);
    income += s?.sell_price ?? s?.price ?? 0;
    cost += players.get(i)?.price ?? 0;
  }
  const bankLeft = Math.round((bank + income - cost) * 10) / 10;
  const hits = Math.max(0, moves.length - freeTransfers);
  return { bankLeft, hits, hitCost: hits * HIT_COST, freeLeft: Math.max(0, freeTransfers - moves.length) };
}

/** The first reason FPL would refuse the draft (club limit or money), in plain words, or null.
    The server checks everything again; this just warns before sending. */
export function draftProblem(
  moves: Pair[],
  squad: Priced[],
  players: Map<number, Priced>,
  bank: number,
): string | null {
  const swap = new Map(moves);
  const after = squad.map((p) => (swap.has(p.id) ? players.get(swap.get(p.id)!) : p));
  const clubs = new Map<string, number>();
  for (const p of after) if (p?.team) clubs.set(p.team, (clubs.get(p.team) || 0) + 1);
  for (const [club, n] of clubs) if (n > 3) return `That's ${n} ${club} players. FPL allows at most 3 from one club.`;
  const { bankLeft } = draftCost(moves, squad, players, bank, 0);
  if (bankLeft < 0) return `That's £${(-bankLeft).toFixed(1)}m more than you have.`;
  return null;
}
