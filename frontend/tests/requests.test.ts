// Starting the advice request early must never send the same request twice (issue #1).
// Run by scripts/check.sh with Node's built-in test runner.
import assert from "node:assert/strict";
import { test } from "node:test";
import { adviceUrl, prefetchAdvice, type Reply, requestAdvice, requestCache, retryable } from "../src/lib/requests.ts";

function counting(replies: Reply[]) {
  const sent: string[] = [];
  const send = (url: string) => {
    sent.push(url);
    return Promise.resolve(replies[Math.min(sent.length - 1, replies.length - 1)]);
  };
  return { sent, send };
}

const tick = () => new Promise((r) => setTimeout(r, 0));

test("the same URL while a request is on its way is sent once", async () => {
  const { sent, send } = counting([{ status: 200, body: { ok: 1 } }]);
  const c = requestCache(send, 1000);
  const a = c.get("/api/advice?team=1");
  const b = c.get("/api/advice?team=1");
  assert.equal(a, b);
  assert.deepEqual(await b, { status: 200, body: { ok: 1 } });
  assert.equal(sent.length, 1);
});

test("an answer is reused until it is older than the TTL", async () => {
  let clock = 0;
  const { sent, send } = counting([{ status: 200, body: {} }]);
  const c = requestCache(send, 1000, () => clock);
  await c.get("/u");
  clock = 999;
  await c.get("/u");
  assert.equal(sent.length, 1);
  clock = 1000;
  await c.get("/u");
  assert.equal(sent.length, 2);
});

test("different URLs are separate requests", async () => {
  const { sent, send } = counting([{ status: 200, body: {} }]);
  const c = requestCache(send, 1000);
  await Promise.all([c.get(adviceUrl(1, null)), c.get(adviceUrl(1, 5)), c.get(adviceUrl(2, null))]);
  assert.equal(sent.length, 3);
});

test("failures worth retrying are forgotten; other answers are kept", async () => {
  for (const r of [{ failed: "network" }, { failed: "timeout" }, { status: 429, body: null }, { status: 503, body: null }] as Reply[]) {
    assert.ok(retryable(r));
    const { sent, send } = counting([r, { status: 200, body: {} }]);
    const c = requestCache(send, 1000);
    await c.get("/u");
    await tick();
    assert.deepEqual(await c.get("/u"), { status: 200, body: {} });
    assert.equal(sent.length, 2);
  }
  const { sent, send } = counting([{ status: 404, body: { error: "not found" } }]);
  const c = requestCache(send, 1000);
  await c.get("/u");
  await tick();
  await c.get("/u");
  assert.equal(sent.length, 1); // a 404 would only say the same again
});

test("one URL per team and league, whatever order they come in", () => {
  assert.equal(adviceUrl(7, null), "/api/advice?team=7");
  assert.equal(adviceUrl(7, 0), "/api/advice?team=7");
  assert.equal(adviceUrl(7, 42), "/api/advice?team=7&league=42");
});

test("an early request is picked up by the button: fetch is called once", async () => {
  const calls: string[] = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = (async (url: string) => {
    calls.push(String(url));
    await new Promise((r) => setTimeout(r, 20)); // the server is still working
    return new Response(JSON.stringify({ team: { name: "x" } }), { status: 200 });
  }) as typeof fetch;
  try {
    prefetchAdvice(123, null); // page load with a saved team
    prefetchAdvice(123, null); // the visitor paused on the same ID
    const r = await requestAdvice(123, null); // "Get my advice", while the first is on its way
    assert.deepEqual(r, { status: 200, body: { team: { name: "x" } } });
    await requestAdvice(123, null); // pressed again later
    assert.deepEqual(calls, ["/api/advice?team=123"]);
    await requestAdvice(123, 9); // a league added: a different request
    assert.equal(calls.length, 2);
  } finally {
    globalThis.fetch = realFetch;
  }
});
