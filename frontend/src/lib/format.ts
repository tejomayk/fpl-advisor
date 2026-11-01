import type { Player } from "./types";

export const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
export const f1 = (v: unknown) => (isNum(v) ? v.toFixed(1) : "–");
export const int = (v: unknown) => (isNum(v) ? Math.round(v).toLocaleString() : "–");
export const money = (v: unknown) => (isNum(v) ? `£${v.toFixed(1)}m` : "–");
export const pct = (v: unknown) => (isNum(v) ? `${Math.round(v * 100)}%` : "–");
export const clamp01 = (v: unknown) => Math.max(0, Math.min(1, isNum(v) ? v : 0));
export const signed = (v: unknown, d = 1) =>
  isNum(v) ? `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(d)}` : "–";

export function ordinal(n: number) {
  const s = ["th", "st", "nd", "rd"];
  const v = n % 100;
  return s[(v - 20) % 10] || s[v] || s[0];
}

export function horizonLabel(h?: number[]) {
  if (!Array.isArray(h) || !h.length) return "";
  return h.length === 1 ? `GW${h[0]}` : `GW${h[0]}–${h[h.length - 1]}`;
}

const pad2 = (n: number) => String(n).padStart(2, "0");
export function clock(ms: number) {
  if (ms <= 0) return "0:00:00";
  const s = Math.floor(ms / 1000);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  return `${d > 0 ? `${d}d ` : ""}${d > 0 ? pad2(h) : h}:${pad2(m)}:${pad2(s % 60)}`;
}

export const dateTime = new Intl.DateTimeFormat(undefined, {
  weekday: "short",
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
});
export const shortDate = (iso?: string) => {
  const t = new Date(String(iso || ""));
  return Number.isNaN(t.getTime())
    ? ""
    : t.toLocaleDateString(undefined, { day: "numeric", month: "short" });
};

/** Injury / suspension state, or null when the player is fully available. */
export function availability(p?: Player | null) {
  if (!p) return null;
  const doubtful = (p.status && p.status !== "a") || (isNum(p.chance) && p.chance < 100);
  if (!doubtful) return null;
  const bad = ["i", "s", "u", "n"].includes(p.status || "") || p.chance === 0;
  const text =
    p.news || (isNum(p.chance) ? `${p.chance}% chance of playing` : "Availability in doubt");
  return { kind: bad ? ("bad" as const) : ("warn" as const), text };
}

/** "LIV(a) HUL(H)" -> [{opp: "LIV", home: false}, ...]. Double gameweeks keep their order. */
export function parseFixtures(s?: string) {
  if (!s) return [];
  return s
    .split(/\s+/)
    .filter(Boolean)
    .map((tok) => {
      const m = tok.match(/^([A-Za-z]{2,4})\((h|a)\)$/i);
      return m ? { opp: m[1].toUpperCase(), home: m[2] === "H" || m[2] === "h" } : { opp: tok, home: null };
    });
}

/** Only web links reach an href: javascript:, data: and junk become null. */
export function safeUrl(u: unknown): string | null {
  if (typeof u !== "string" || !u) return null;
  try {
    const x = new URL(u, location.href);
    return x.protocol === "https:" || x.protocol === "http:" ? x.href : null;
  } catch {
    return null;
  }
}

export const capitalise = (s?: string) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : "");

export const CHIP_SHORT: Record<string, string> = {
  Wildcard: "WC",
  "Free Hit": "FH",
  "Bench Boost": "BB",
  "Triple Captain": "TC",
  wildcard: "WC",
  freehit: "FH",
  free_hit: "FH",
  bboost: "BB",
  bench_boost: "BB",
  "3xc": "TC",
  triple_captain: "TC",
};
export const CHIP_LABEL: Record<string, string> = {
  wildcard: "Wildcard",
  free_hit: "Free Hit",
  freehit: "Free Hit",
  bench_boost: "Bench Boost",
  bboost: "Bench Boost",
  triple_captain: "Triple Captain",
  "3xc": "Triple Captain",
};
