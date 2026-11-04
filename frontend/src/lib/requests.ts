// Advice requests, shared between "start early" and "Get my advice" (issue #1).
// A new team's advice takes several seconds on the server, so the page starts the request as soon
// as it knows the team: when it opens with a saved team, or when the visitor pauses after typing a
// valid ID. Pressing the button then picks up that same request instead of sending another.
// No imports, so Node's test runner can load it as it is (frontend/tests/requests.test.ts).

/** What came back: the HTTP status and JSON body, or why there was no answer. */
export type Reply = { status: number; body: unknown } | { failed: "timeout" | "network" };

/** Worth asking again: no answer, too many requests, or a server error. A 4xx answer would only
    repeat itself. */
export function retryable(r: Reply): boolean {
  return "failed" in r || r.status === 429 || r.status >= 500;
}

/** Remembers each request by URL. While one is on its way, or after it answered within `ttlMs`,
    asking again returns the same promise; nothing is sent twice. Retryable failures are
    forgotten as soon as they arrive, so "Try again" asks again. */
export function requestCache(send: (url: string) => Promise<Reply>, ttlMs: number, now: () => number = Date.now) {
  const entries = new Map<string, { at: number; reply: Promise<Reply> }>();
  return {
    get(url: string): Promise<Reply> {
      const hit = entries.get(url);
      if (hit && now() - hit.at < ttlMs) return hit.reply;
      const entry = { at: now(), reply: send(url) };
      entries.set(url, entry);
      entry.reply.then((r) => {
        if (retryable(r) && entries.get(url) === entry) entries.delete(url);
      });
      return entry.reply;
    },
  };
}

/** GET a JSON endpoint once, giving up after `timeoutMs`. Never throws. */
export async function sendJson(url: string, timeoutMs = 120000): Promise<Reply> {
  const ctrl = new AbortController();
  const timeout = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, { signal: ctrl.signal, headers: { Accept: "application/json" } });
    const body = await res.json().catch(() => null);
    return { status: res.status, body };
  } catch (e) {
    return { failed: e instanceof DOMException && e.name === "AbortError" ? "timeout" : "network" };
  } finally {
    clearTimeout(timeout);
  }
}

/** The one URL for a team's advice, so an early request and the button's request match. */
export function adviceUrl(team: number, league: number | null): string {
  const qs = new URLSearchParams({ team: String(team) });
  if (league) qs.set("league", String(league));
  return `/api/advice?${qs}`;
}

// The server keeps advice for 10 minutes (web.py ADVICE_TTL_SECONDS); after that, ask again.
const ADVICE_TTL_MS = 10 * 60 * 1000;
const advice = requestCache((url) => sendJson(url), ADVICE_TTL_MS);

/** The advice for a team: the request already on its way, or a new one. */
export function requestAdvice(team: number, league: number | null): Promise<Reply> {
  return advice.get(adviceUrl(team, league));
}

/** Start the advice request early, before the visitor asks for it. */
export function prefetchAdvice(team: number, league: number | null): void {
  void advice.get(adviceUrl(team, league));
}
