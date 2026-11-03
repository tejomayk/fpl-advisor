// Draft keying, expiry, links and costs. Run by scripts/check.sh with Node's built-in test runner
// (no extra packages): node --test frontend/tests/
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  clearDraft,
  decodeDraft,
  draftCost,
  draftKey,
  draftMatchesSquad,
  draftProblem,
  encodeDraft,
  type KV,
  movesParam,
  type Pair,
  readDraft,
  saveDraft,
  takeOldDrafts,
} from "../src/lib/drafts.ts";

function memory(): KV & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (k) => data.get(k) ?? null,
    setItem: (k, v) => void data.set(k, v),
    removeItem: (k) => void data.delete(k),
    key: (i) => [...data.keys()][i] ?? null,
    get length() {
      return data.size;
    },
  };
}

const DEADLINE = "2026-11-07T11:00:00Z";
const BEFORE = Date.parse(DEADLINE) - 3600e3;
const AFTER = Date.parse(DEADLINE) + 60e3;
const MOVES: Pair[] = [
  [301, 55],
  [7, 200],
];

test("one draft per team per gameweek", () => {
  const s = memory();
  saveDraft(s, { team: 11, gw: 10, deadline: DEADLINE, moves: MOVES }, BEFORE);
  saveDraft(s, { team: 11, gw: 10, deadline: DEADLINE, moves: [[1, 2]] }, BEFORE);
  saveDraft(s, { team: 22, gw: 10, deadline: DEADLINE, moves: MOVES }, BEFORE);
  saveDraft(s, { team: 98765432, gw: 10, deadline: DEADLINE, moves: MOVES }, BEFORE); // 8-digit IDs
  assert.equal(s.data.size, 3);
  assert.ok(s.data.has(draftKey(11, 10)) && s.data.has(draftKey(22, 10)));
  assert.deepEqual(readDraft(s, 98765432, 10, BEFORE)?.moves, MOVES);
  assert.deepEqual(takeOldDrafts(s, 98765432, 10, BEFORE), []);
  assert.ok(readDraft(s, 98765432, 10, BEFORE));
  assert.deepEqual(readDraft(s, 11, 10, BEFORE)?.moves, [[1, 2]]);
  assert.deepEqual(readDraft(s, 22, 10, BEFORE)?.moves, MOVES);
  assert.equal(readDraft(s, 11, 11, BEFORE), null);
  clearDraft(s, 11, 10);
  assert.equal(readDraft(s, 11, 10, BEFORE), null);
});

test("a draft is dropped once its deadline passes", () => {
  const s = memory();
  saveDraft(s, { team: 11, gw: 10, deadline: DEADLINE, moves: MOVES }, BEFORE);
  assert.ok(readDraft(s, 11, 10, BEFORE));
  assert.equal(readDraft(s, 11, 10, AFTER), null);
  assert.equal(s.data.size, 0);
});

test("old drafts are handed back once, then gone", () => {
  const s = memory();
  saveDraft(s, { team: 11, gw: 9, deadline: "2026-10-31T11:00:00Z", moves: MOVES }, BEFORE);
  saveDraft(s, { team: 11, gw: 10, deadline: DEADLINE, moves: [[1, 2]] }, BEFORE);
  saveDraft(s, { team: 22, gw: 9, deadline: "2026-10-31T11:00:00Z", moves: MOVES }, BEFORE);
  s.setItem(draftKey(11, 8), "not json");
  const old = takeOldDrafts(s, 11, 10, BEFORE);
  assert.deepEqual(
    old.map((d) => d.gw),
    [9],
  );
  assert.equal(readDraft(s, 11, 10, BEFORE)?.gw, 10); // this week's draft stays
  assert.ok(s.data.has(draftKey(22, 9))); // other teams are left alone
  assert.ok(!s.data.has(draftKey(11, 8))); // junk is cleared
  assert.deepEqual(takeOldDrafts(s, 11, 10, BEFORE), []);
});

test("the real squad is checked against an old draft", () => {
  const squad = [55, 200, 3, 4];
  assert.equal(draftMatchesSquad({ moves: MOVES }, squad), true);
  assert.equal(draftMatchesSquad({ moves: MOVES }, [55, 7, 3, 4]), false); // kept 7
  assert.equal(draftMatchesSquad({ moves: MOVES }, [301, 55, 200]), false); // kept 301
});

test("links hold player IDs only and round-trip", () => {
  const s = encodeDraft(10, MOVES);
  assert.equal(s, "10_301-55_7-200");
  assert.deepEqual(decodeDraft(s), { gw: 10, moves: MOVES });
  assert.equal(movesParam(MOVES), "301-55,7-200");
  for (const bad of ["", "10", "x_1-2", "10_1-2-3", "10_a-b", "10_1-2_", `10_${"1-2_".repeat(60)}`]) {
    assert.equal(decodeDraft(bad), null, bad);
  }
});

test("money, free transfers and hits before confirming", () => {
  const squad = [
    { id: 1, team: "ARS", price: 8.0, sell_price: 7.8 },
    { id: 2, team: "CHE", price: 5.0, sell_price: 5.0 },
  ];
  const players = new Map([
    [10, { id: 10, team: "LIV", price: 9.0 }],
    [11, { id: 11, team: "ARS", price: 4.5 }],
  ]);
  assert.deepEqual(draftCost([[1, 10]], squad, players, 1.0, 1), { bankLeft: -0.2, hits: 0, hitCost: 0, freeLeft: 0 });
  assert.deepEqual(
    draftCost(
      [
        [1, 10],
        [2, 11],
      ],
      squad,
      players,
      1.0,
      1,
    ),
    { bankLeft: 0.3, hits: 1, hitCost: 4, freeLeft: 0 },
  );
  assert.match(draftProblem([[1, 10]], squad, players, 1.0) || "", /£0.2m more than you have/);
  assert.equal(draftProblem([[1, 10]], squad, players, 2.0), null);
});
