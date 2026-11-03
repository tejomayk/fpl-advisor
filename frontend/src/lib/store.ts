import { useCallback, useEffect, useRef, useState } from "react";
import type { Advice, ListedPlayer } from "./types";
import { isNum } from "./format";

export type Status =
  | { kind: "idle" }
  | { kind: "loading"; demo: boolean; team?: number; league?: number | null; slow: 0 | 1 | 2 }
  | { kind: "error"; message: string; team?: number; league?: number | null; demo: boolean }
  | { kind: "ready"; data: Advice; demo: boolean; team?: number; league?: number | null };

const K_TEAM = "fpl-advisor.team";
const K_LEAGUE = "fpl-advisor.league";
export const saved = {
  get(k: "team" | "league") {
    try {
      return localStorage.getItem(k === "team" ? K_TEAM : K_LEAGUE) || "";
    } catch {
      return "";
    }
  },
  set(team: number, league: number | null) {
    try {
      localStorage.setItem(K_TEAM, String(team));
      if (league) localStorage.setItem(K_LEAGUE, String(league));
      else localStorage.removeItem(K_LEAGUE);
    } catch {
      /* storage blocked */
    }
  },
};

function errorMessage(status: number, body: unknown) {
  if (status === 429) return "Too many requests. Wait a minute and try again.";
  const b = body as { error?: unknown; detail?: unknown } | null;
  if (b && typeof b.error === "string" && b.error) return b.error;
  if (status === 422 || (b && Array.isArray(b.detail)))
    return "That ID doesn't look right. IDs are whole numbers, up to 8 digits.";
  if (status === 404) return "We couldn't find that team or league. Check the number in your FPL link.";
  if (status >= 500) return "Something went wrong on our side. Try again in a minute.";
  return "Something went wrong. Please try again.";
}

/** Writes the current team, league and view into the address bar (no reload, no history entry).
    `draft` (a what-if draft link, see lib/drafts.ts) is kept as it is unless given; null drops it. */
export function writeUrl(p: {
  team?: number | null;
  league?: number | null;
  demo?: boolean;
  view?: string;
  draft?: string | null;
}) {
  const qs = new URLSearchParams();
  if (p.demo) qs.set("demo", "1");
  else {
    if (p.team) qs.set("team", String(p.team));
    if (p.team && p.league) qs.set("league", String(p.league));
  }
  if (p.view && p.view !== "week" && (p.demo || p.team)) qs.set("view", p.view);
  const draft = p.draft === undefined ? new URLSearchParams(location.search).get("draft") : p.draft;
  if (draft && p.team && !p.demo) qs.set("draft", draft);
  const s = qs.toString();
  try {
    history.replaceState(null, "", `${location.pathname}${s ? `?${s}` : ""}`);
  } catch {
    /* ignore */
  }
}

/** GET a JSON endpoint: the body, or a plain-words error message. */
export async function getJson<T>(url: string, timeoutMs = 120000): Promise<{ ok: true; data: T } | { ok: false; message: string }> {
  const ctrl = new AbortController();
  const timeout = window.setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, { signal: ctrl.signal, headers: { Accept: "application/json" } });
    const body = await res.json().catch(() => null);
    if (!res.ok || !body || typeof body !== "object") return { ok: false, message: errorMessage(res.status, body) };
    return { ok: true, data: body as T };
  } catch (e) {
    return {
      ok: false,
      message:
        e instanceof DOMException && e.name === "AbortError"
          ? "This is taking too long. FPL may be busy. Try again in a minute."
          : "We couldn't reach the server. Check your connection and try again.",
    };
  } finally {
    clearTimeout(timeout);
  }
}

let playersCache: Promise<ListedPlayer[] | string> | null = null;
/** Every player (for the transfer editor), fetched once per page load. Failures aren't cached. */
export function loadPlayers(): Promise<ListedPlayer[] | string> {
  if (!playersCache) {
    playersCache = getJson<{ players?: ListedPlayer[] }>("/api/players", 60000).then((r) => {
      if (r.ok && Array.isArray(r.data.players)) return r.data.players;
      playersCache = null;
      return r.ok ? "We couldn't load the player list. Try again in a minute." : r.message;
    });
  }
  return playersCache;
}

export function useAdvice() {
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const seq = useRef(0);
  const timers = useRef<number[]>([]);
  const clear = () => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
  };

  const slowTimers = (n: number, base: Status & { kind: "loading" }) => {
    timers.current.push(
      window.setTimeout(() => n === seq.current && setStatus({ ...base, slow: 1 }), 5000),
      window.setTimeout(() => n === seq.current && setStatus({ ...base, slow: 2 }), 35000),
    );
  };

  const load = useCallback(async (team: number, league: number | null) => {
    const n = ++seq.current;
    clear();
    const base = { kind: "loading", demo: false, team, league, slow: 0 } as const;
    setStatus(base);
    slowTimers(n, base);
    const qs = new URLSearchParams({ team: String(team) });
    if (league) qs.set("league", String(league));
    const ctrl = new AbortController();
    const timeout = window.setTimeout(() => ctrl.abort(), 120000);
    let res: Response;
    let body: unknown = null;
    try {
      res = await fetch(`/api/advice?${qs}`, { signal: ctrl.signal, headers: { Accept: "application/json" } });
      body = await res.json().catch(() => null);
    } catch (e) {
      if (n !== seq.current) return;
      clear();
      setStatus({
        kind: "error",
        team,
        league,
        demo: false,
        message:
          e instanceof DOMException && e.name === "AbortError"
            ? "This is taking too long. FPL may be busy. Try again in a minute."
            : "We couldn't reach the server. Check your connection and try again.",
      });
      return;
    } finally {
      clearTimeout(timeout);
    }
    if (n !== seq.current) return;
    clear();
    if (!res.ok || !body || typeof body !== "object") {
      setStatus({ kind: "error", team, league, demo: false, message: errorMessage(res.status, body) });
      return;
    }
    saved.set(team, league);
    setStatus({ kind: "ready", data: body as Advice, demo: false, team, league });
  }, []);

  const loadDemo = useCallback(async () => {
    const n = ++seq.current;
    clear();
    setStatus({ kind: "loading", demo: true, slow: 0 });
    try {
      const res = await fetch("/static/sample_advice.json");
      if (!res.ok) throw new Error(String(res.status));
      const body = (await res.json()) as Advice;
      if (n !== seq.current) return;
      // Keep the sample's clock live: the deadline sits as far ahead as it did in the sample.
      if (body.gameweek && isNum(body.gameweek.hours_to_deadline)) {
        body.gameweek = {
          ...body.gameweek,
          deadline: new Date(Date.now() + body.gameweek.hours_to_deadline * 3600e3).toISOString(),
        };
      }
      setStatus({ kind: "ready", data: body, demo: true });
    } catch {
      if (n === seq.current)
        setStatus({ kind: "error", demo: true, message: "We couldn't load the example. Please refresh." });
    }
  }, []);

  const reset = useCallback(() => {
    ++seq.current;
    clear();
    setStatus({ kind: "idle" });
  }, []);

  useEffect(() => clear, []);
  return { status, load, loadDemo, reset };
}
