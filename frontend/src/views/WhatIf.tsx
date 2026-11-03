// What-if transfers: enter your own transfers (or ones you've already made in FPL), see how they
// score against making none and against the advisor's plan, save the draft in this browser or
// share it as a link. The server stores nothing (src/fpl_advisor/whatif.py does the scoring).
import { AnimatePresence, m } from "motion/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Md } from "../components/Md";
import { useCloseSheet, useSheet } from "../components/Sheet";
import { Token } from "../components/Token";
import { ConfDots, Section, StatusDot } from "../components/ui";
import {
  browserStore,
  clearDraft,
  decodeDraft,
  draftCost,
  draftMatchesSquad,
  draftProblem,
  encodeDraft,
  MAX_MOVES,
  movesParam,
  type Pair,
  readDraft,
  saveDraft,
  takeOldDrafts,
} from "../lib/drafts";
import { CONF_LABEL, confKey, f1, horizonLabel, isNum, money, moneyText, pct } from "../lib/format";
import { getJson, loadPlayers, writeUrl } from "../lib/store";
import type { Advice, ListedPlayer, Player, WhatIf as WhatIfData } from "../lib/types";

const PENDING_FALLBACK = "FPL only shows your squad after each deadline";

const POS: [string, string][] = [
  ["GKP", "Goalkeepers"],
  ["DEF", "Defenders"],
  ["MID", "Midfielders"],
  ["FWD", "Forwards"],
];
const POS_WORD: Record<string, string> = { GKP: "goalkeepers", DEF: "defenders", MID: "midfielders", FWD: "forwards" };

const same = (a: Pair[], b: Pair[]) =>
  a.length === b.length && a.every(([o, i], k) => b[k][0] === o && b[k][1] === i);

/** The note about transfers FPL can't show yet: the payload's field, or (older payloads) the note. */
export function pendingNote(d: Advice): string | null {
  if (d.initial) return null;
  return d.pending_note || (d.notes || []).find((n) => n.startsWith(PENDING_FALLBACK)) || null;
}

type Result =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; data: WhatIfData };

export interface WhatIfState {
  /** False for the example team, a squad picked from scratch or an older payload. */
  available: boolean;
  draft: Pair[] | null;
  saved: boolean;
  result: Result;
  note: string | null;
  dismissNote: () => void;
  score: (moves: Pair[]) => void;
  save: () => void;
  discard: () => void;
  link: () => string | null;
}

/** Draft state for one team's advice. Lives above the tabs, so switching views keeps it. */
export function useWhatIf(d: Advice, team: number | undefined, league: number | null | undefined, linkDraft: string | null): WhatIfState {
  const gw = d.gameweek?.id;
  const deadline = d.gameweek?.deadline || "";
  const squad = d.squad || [];
  const available = Boolean(team && isNum(gw) && squad.length === 15 && !d.initial);
  const [draft, setDraft] = useState<Pair[] | null>(null);
  const [saved, setSaved] = useState(false);
  const [result, setResult] = useState<Result>({ kind: "idle" });
  const [note, setNote] = useState<string | null>(null);
  const seq = useRef(0);

  const score = useCallback(
    async (moves: Pair[]) => {
      if (!team) return;
      const n = ++seq.current;
      setDraft(moves);
      const s = isNum(gw) ? readDraft(browserStore(), team, gw) : null;
      setSaved(Boolean(s && same(s.moves, moves)));
      setResult({ kind: "loading" });
      const qs = new URLSearchParams({ team: String(team), moves: movesParam(moves) });
      if (league) qs.set("league", String(league));
      const r = await getJson<WhatIfData>(`/api/whatif?${qs}`);
      if (n !== seq.current) return;
      setResult(r.ok ? { kind: "ready", data: r.data } : { kind: "error", message: r.message });
    },
    [team, league, gw],
  );

  // On load: tidy drafts from past gameweeks, then open a shared link or this week's saved draft.
  const booted = useRef(false);
  useEffect(() => {
    if (booted.current || !available || !team || !isNum(gw)) return;
    booted.current = true;
    const store = browserStore();
    const ids = squad.map((p) => p.id);
    const wrong = takeOldDrafts(store, team, gw).find((o) => !draftMatchesSquad(o, ids));
    if (wrong)
      setNote(
        `Your saved draft for GW${wrong.gw} doesn't match the squad FPL shows now that the deadline has passed, so it's been cleared.`,
      );
    if (linkDraft) {
      const link = decodeDraft(linkDraft);
      if (link && link.gw === gw) {
        score(link.moves);
        return;
      }
      setNote(link ? `That draft link was for GW${link.gw}. Its deadline has passed, so it no longer applies.` : "That draft link didn't look right, so it was ignored.");
      writeUrl({ team, league, draft: null });
    }
    const s = readDraft(store, team, gw);
    if (s) score(s.moves);
  }, [available, team, league, gw, squad, linkDraft, score]);

  const save = useCallback(() => {
    if (!team || !isNum(gw) || !draft) return;
    if (saveDraft(browserStore(), { team, gw, deadline, moves: draft })) setSaved(true);
    else setNote("This browser blocks saving. Use Copy link to keep the draft instead.");
  }, [team, gw, deadline, draft]);

  const discard = useCallback(() => {
    ++seq.current;
    if (team && isNum(gw)) clearDraft(browserStore(), team, gw);
    setDraft(null);
    setSaved(false);
    setResult({ kind: "idle" });
    if (new URLSearchParams(location.search).get("draft")) writeUrl({ team, league, draft: null });
  }, [team, league, gw]);

  const link = useCallback(() => {
    if (!team || !isNum(gw) || !draft?.length) return null;
    const qs = new URLSearchParams({ team: String(team) });
    if (league) qs.set("league", String(league));
    qs.set("draft", encodeDraft(gw, draft));
    return `${location.origin}${location.pathname}?${qs}`;
  }, [team, league, gw, draft]);

  return { available, draft, saved, result, note, dismissNote: () => setNote(null), score, save, discard, link };
}

/* ---------------- The note at the top of the advice ---------------- */

export function PendingNote({ d, wi }: { d: Advice; wi: WhatIfState }) {
  const show = useSheet();
  const text = pendingNote(d);
  if (!text && !wi.available) return null;
  const open = () => show(editorSheet(d, wi));
  return (
    <m.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ type: "spring", stiffness: 220, damping: 28 }}
      className="mt-6 rounded-[24px] bg-raised px-5 py-4 md:mt-10"
    >
      {text && (
        <p className="text-[15px] leading-relaxed text-fg-2">
          <Md text={text} />
        </p>
      )}
      {wi.available && (
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2">
          <m.button
            type="button"
            whileTap={{ scale: 0.97 }}
            onClick={open}
            className="inline-flex h-10 items-center rounded-full bg-fg px-4 text-[15px] font-semibold text-bg"
          >
            {wi.draft?.length ? "Edit your transfers" : "Enter transfers you've already made"}
          </m.button>
          {!wi.draft?.length && <span className="text-[13px] text-fg-3">or try your own ideas against the advisor's plan</span>}
        </div>
      )}
      <AnimatePresence>
        {wi.note && (
          <m.p
            role="status"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="mt-3 flex items-start gap-3 overflow-hidden text-[15px] text-fg"
          >
            <span className="flex-1">{wi.note}</span>
            <button type="button" onClick={wi.dismissNote} className="shrink-0 text-[13px] text-fg-3 underline underline-offset-4">
              OK
            </button>
          </m.p>
        )}
      </AnimatePresence>
    </m.div>
  );
}

/* ---------------- The editor (a sheet) ---------------- */

function editorSheet(d: Advice, wi: WhatIfState) {
  return {
    title: "Your transfers",
    body: (
      <Editor
        squad={d.squad || []}
        bank={d.team?.bank ?? 0}
        ft={d.team?.free_transfers ?? 1}
        initial={wi.draft || []}
        onScore={wi.score}
        onClear={wi.discard}
      />
    ),
  };
}

function Editor({
  squad,
  bank,
  ft,
  initial,
  onScore,
  onClear,
}: {
  squad: Player[];
  bank: number;
  ft: number;
  initial: Pair[];
  onScore: (moves: Pair[]) => void;
  onClear: () => void;
}) {
  const close = useCloseSheet();
  const [moves, setMoves] = useState<Pair[]>(initial);
  const [picking, setPicking] = useState<number | null>(null);
  const [players, setPlayers] = useState<ListedPlayer[] | string | null>(null);
  useEffect(() => {
    let live = true;
    loadPlayers().then((r) => live && setPlayers(r));
    return () => {
      live = false;
    };
  }, []);
  const byId = useMemo(() => new Map((Array.isArray(players) ? players : []).map((p) => [p.id, p])), [players]);
  const cost = draftCost(moves, squad, byId, bank, ft);
  const problem = Array.isArray(players) ? draftProblem(moves, squad, byId, bank) : null;
  const swap = new Map(moves);

  if (picking !== null) {
    const out = squad.find((p) => p.id === picking)!;
    return (
      <Picker
        out={out}
        squad={squad}
        moves={moves}
        players={players}
        bankLeft={cost.bankLeft}
        onBack={() => setPicking(null)}
        onPick={(id) => {
          setMoves([...moves.filter(([o]) => o !== picking), [picking, id]]);
          setPicking(null);
        }}
      />
    );
  }

  return (
    <div>
      <p className="text-[15px] leading-relaxed text-fg-2">
        Tap <span className="font-medium text-fg">Sell</span> on a player, then pick who comes in. Count transfers you've
        already made in FPL as well as ideas you want to test.
      </p>
      <dl className="num sticky top-0 z-10 mt-5 grid grid-cols-3 gap-2 bg-panel py-2 text-center">
        <SummaryCell k="Bank left" v={money(cost.bankLeft)} bad={cost.bankLeft < 0} />
        <SummaryCell k="Free transfers left" v={String(cost.freeLeft)} />
        <SummaryCell k="Points hit" v={cost.hitCost ? `−${cost.hitCost}` : "0"} bad={cost.hitCost > 0} />
      </dl>
      {cost.hits > 0 && (
        <p className="mt-1 text-[13px] text-fg-3">
          You have {ft} free {ft === 1 ? "transfer" : "transfers"}; each one beyond that costs 4 points.
        </p>
      )}
      {typeof players === "string" && <p className="mt-3 rounded-2xl bg-red-soft px-4 py-3 text-[15px]">{players}</p>}
      {POS.map(([pos, label]) => {
        const ps = squad.filter((p) => p.pos === pos);
        if (!ps.length) return null;
        return (
          <div key={pos} className="mt-6">
            <h3 className="mb-1 text-[13px] text-fg-3">{label}</h3>
            <ul>
              {ps.map((p) => {
                const inn = swap.has(p.id) ? byId.get(swap.get(p.id)!) : undefined;
                return (
                  <li key={p.id} className="flex min-h-14 items-center gap-3 border-t border-line py-2 first:border-t-0">
                    <Token team={inn?.team ?? p.team} size={28} label={false} dim={!inn && swap.has(p.id)} />
                    <span className="min-w-0 flex-1">
                      {swap.has(p.id) ? (
                        <>
                          <span className="block truncate text-[13px] text-fg-3 line-through">{p.name}</span>
                          <span className="block truncate text-[16px] font-semibold">{inn?.name ?? `Player ${swap.get(p.id)}`}</span>
                        </>
                      ) : (
                        <span className="flex items-center gap-2">
                          <span className="truncate text-[16px]">{p.name}</span>
                          <StatusDot p={p} />
                        </span>
                      )}
                    </span>
                    <span className="num shrink-0 text-right text-[13px] text-fg-2">
                      {inn ? money(inn.price) : money(p.sell_price ?? p.price)}
                      <span className="block text-[11px] text-fg-3">{inn ? "costs" : "sells for"}</span>
                    </span>
                    {swap.has(p.id) ? (
                      <button
                        type="button"
                        onClick={() => setMoves(moves.filter(([o]) => o !== p.id))}
                        className="h-9 shrink-0 rounded-full bg-raised px-3 text-[14px] font-medium"
                        aria-label={`Undo: keep ${p.name}`}
                      >
                        Undo
                      </button>
                    ) : (
                      <button
                        type="button"
                        disabled={moves.length >= MAX_MOVES || !Array.isArray(players)}
                        onClick={() => setPicking(p.id)}
                        className="h-9 shrink-0 rounded-full bg-raised px-3 text-[14px] font-medium disabled:opacity-40"
                        aria-label={`Sell ${p.name}`}
                      >
                        Sell
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
      <div className="sticky bottom-0 mt-6 bg-panel pt-3 pb-1">
        {problem && <p className="mb-3 rounded-2xl bg-red-soft px-4 py-3 text-[15px]">{problem}</p>}
        <m.button
          type="button"
          whileTap={{ scale: 0.98 }}
          disabled={!moves.length || Boolean(problem)}
          onClick={() => {
            close();
            onScore(moves);
          }}
          className="h-12 w-full rounded-full bg-grass text-[16px] font-semibold text-on-grass disabled:opacity-40"
        >
          {moves.length
            ? `Score ${moves.length} ${moves.length === 1 ? "transfer" : "transfers"}${cost.hitCost ? ` (−${cost.hitCost} hit)` : ""}`
            : "Pick a player to sell"}
        </m.button>
        {initial.length > 0 && (
          <button
            type="button"
            onClick={() => {
              close();
              onClear();
            }}
            className="mt-3 w-full text-center text-[14px] text-fg-3 underline underline-offset-4"
          >
            Clear this draft
          </button>
        )}
      </div>
    </div>
  );
}

function SummaryCell({ k, v, bad }: { k: string; v: string; bad?: boolean }) {
  return (
    <div className="rounded-2xl bg-raised px-2 py-2.5">
      <dt className="text-[11px] leading-tight text-fg-3">{k}</dt>
      <dd className={`mt-1 text-[17px] font-semibold ${bad ? "text-red" : ""}`}>{v}</dd>
    </div>
  );
}

function Picker({
  out,
  squad,
  moves,
  players,
  bankLeft,
  onBack,
  onPick,
}: {
  out: Player;
  squad: Player[];
  moves: Pair[];
  players: ListedPlayer[] | string | null;
  bankLeft: number;
  onBack: () => void;
  onPick: (id: number) => void;
}) {
  const [q, setQ] = useState("");
  const sells = out.sell_price ?? out.price ?? 0;
  // Money available for this signing: the bank after every other move, plus this sale.
  const current = moves.find(([o]) => o === out.id);
  const list = Array.isArray(players) ? players : [];
  const currentCost = current ? list.find((p) => p.id === current[1])?.price ?? 0 : 0;
  const budget = Math.round((bankLeft + (current ? currentCost : sells)) * 10) / 10;
  const taken = new Set([...squad.map((p) => p.id), ...moves.filter(([o]) => o !== out.id).map(([, i]) => i)]);
  const needle = q.trim().toLowerCase();
  const rows = list
    .filter((p) => p.pos === out.pos && !taken.has(p.id))
    .filter((p) => !needle || p.name.toLowerCase().includes(needle) || (p.team || "").toLowerCase().includes(needle))
    .sort((a, b) => (b.price ?? 0) - (a.price ?? 0) || (b.selected_by ?? 0) - (a.selected_by ?? 0))
    .slice(0, 80);
  return (
    <div>
      <button type="button" onClick={onBack} className="inline-flex items-center gap-1.5 text-[14px] text-fg-2">
        <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
          <path d="M15 6l-6 6 6 6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        Back to your squad
      </button>
      <p className="mt-3 text-[15px] leading-relaxed text-fg-2">
        Selling <span className="font-semibold text-fg">{out.name}</span> for {money(sells)}. You can spend up to{" "}
        <span className="num font-semibold text-fg">{money(budget)}</span>.
      </p>
      <input
        type="search"
        autoFocus
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder={`Search ${POS_WORD[out.pos || ""] || "players"} by name or club`}
        aria-label="Search players"
        className="mt-4 h-12 w-full rounded-2xl bg-raised px-4 text-[16px] outline-none placeholder:text-fg-3 focus:ring-2 focus:ring-grass"
      />
      {players === null && <p className="mt-6 text-[15px] text-fg-3">Loading players…</p>}
      {typeof players === "string" && <p className="mt-4 rounded-2xl bg-red-soft px-4 py-3 text-[15px]">{players}</p>}
      <ul className="mt-3">
        {rows.map((p) => {
          const left = Math.round((budget - (p.price ?? 0)) * 10) / 10;
          return (
            <li key={p.id} className="border-t border-line first:border-t-0">
              <button
                type="button"
                onClick={() => onPick(p.id)}
                className="-mx-2 flex w-[calc(100%+1rem)] items-center gap-3 rounded-xl px-2 py-2.5 text-left hover:bg-raised"
                aria-label={`Buy ${p.name}, ${money(p.price)}`}
              >
                <Token team={p.team} size={28} label={false} />
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-2">
                    <span className="truncate text-[16px]">{p.name}</span>
                    <StatusDot p={p} />
                  </span>
                  <span className="block text-[12px] text-fg-3">
                    {p.team}
                    {isNum(p.selected_by) ? ` · ${p.selected_by.toFixed(1)}% own` : ""}
                  </span>
                </span>
                <span className="num shrink-0 text-right">
                  <span className="block text-[15px] font-semibold">{money(p.price)}</span>
                  <span className={`block text-[12px] ${left < 0 ? "text-red" : "text-fg-3"}`}>
                    {left < 0 ? `£${(-left).toFixed(1)}m short` : `${money(left)} left`}
                  </span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      {Array.isArray(players) && !rows.length && <p className="mt-6 text-[15px] text-fg-3">No one matches “{q}”.</p>}
    </div>
  );
}

/* ---------------- The result ---------------- */

export function WhatIfPanel({ d, wi }: { d: Advice; wi: WhatIfState }) {
  const show = useSheet();
  const [copied, setCopied] = useState<string | null>(null);
  if (!wi.available || !wi.draft?.length || wi.result.kind === "idle") return null;
  const r = wi.result;
  const hz = horizonLabel(d.horizon);
  const copy = async () => {
    const url = wi.link();
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      setCopied("Link copied");
    } catch {
      setCopied(url); // shown for copying by hand
    }
  };
  return (
    <Section
      title="Your transfers"
      sub={`Expected points over ${hz || "the next few gameweeks"}, after hits`}
      meta={wi.saved ? "Saved on this device" : undefined}
      className="mt-12 md:mt-16"
    >
      {r.kind === "loading" && (
        <div role="status" aria-live="polite" className="py-6">
          <div className="grid grid-cols-3 gap-2">
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-24 animate-pulse rounded-2xl bg-raised" />
            ))}
          </div>
          <p className="mt-4 text-[15px] text-fg-2">Scoring your transfers… The first try can take up to half a minute.</p>
        </div>
      )}
      {r.kind === "error" && (
        <div role="alert" className="py-4">
          <p className="rounded-2xl bg-red-soft px-4 py-3 text-[15px] leading-relaxed">{r.message}</p>
        </div>
      )}
      {r.kind === "ready" && <Result data={r.data} />}
      <div className="mt-5 flex flex-wrap gap-2">
        <m.button
          type="button"
          whileTap={{ scale: 0.97 }}
          onClick={() => show(editorSheet(d, wi))}
          className="h-10 rounded-full bg-fg px-4 text-[15px] font-semibold text-bg"
        >
          Edit
        </m.button>
        <m.button
          type="button"
          whileTap={{ scale: 0.97 }}
          onClick={wi.save}
          disabled={wi.saved}
          className="h-10 rounded-full bg-raised px-4 text-[15px] font-medium disabled:text-fg-3"
        >
          {wi.saved ? "Saved" : "Save"}
        </m.button>
        <m.button type="button" whileTap={{ scale: 0.97 }} onClick={copy} className="h-10 rounded-full bg-raised px-4 text-[15px] font-medium">
          Copy link
        </m.button>
        <m.button type="button" whileTap={{ scale: 0.97 }} onClick={wi.discard} className="h-10 rounded-full px-3 text-[15px] text-fg-3">
          Discard
        </m.button>
      </div>
      {copied && (
        <p role="status" className="mt-3 text-[13px] break-all text-fg-2">
          {copied === "Link copied" ? "Link copied. It holds player IDs only; open it on any device." : <>Copy this link: {copied}</>}
        </p>
      )}
      <p className="mt-3 text-[13px] text-fg-3">
        Save keeps the draft in this browser until the deadline. Nothing is stored on our server.
      </p>
    </Section>
  );
}

function Result({ data }: { data: WhatIfData }) {
  const c = data.compare || {};
  const cols: [string, string, number | undefined, string][] = [
    ["yours", "Your transfers", c.yours?.points, data.hit_cost ? `−${data.hit_cost} hit included` : "no hit"],
    ["hold", "No transfers", c.hold?.points, "keep your squad"],
    ["advisor", "Advisor's plan", c.advisor?.points, planNote(c.advisor?.transfers?.length)],
  ];
  const best = Math.max(...cols.map(([, , v]) => (isNum(v) ? v : -Infinity)));
  const ck = confKey(data.confidence);
  const win = data.win_chance;
  const L = data.lineup;
  const g = data.gameweek?.id;
  return (
    <m.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ type: "spring", stiffness: 220, damping: 28 }}>
      <ul className="mt-2">
        {(data.moves || []).map((mv, k) => (
          <li key={k} className="flex items-center gap-3 border-t border-line py-3 first:border-t-0">
            <span aria-hidden="true" className="flex shrink-0 items-center gap-1">
              <Token team={mv.out?.team} size={24} dim label={false} />
              <Token team={mv.in?.team} size={24} label={false} />
            </span>
            <span className="min-w-0 flex-1 truncate text-[15px] text-fg-2">
              {mv.out?.name} <span aria-hidden="true" className="text-fg-3">→</span>
              <span className="sr-only">to</span> <span className="font-semibold text-fg">{mv.in?.name}</span>
            </span>
            <span className="num shrink-0 text-[13px] text-fg-3">{moneyText(mv.money)}</span>
          </li>
        ))}
      </ul>
      <p className="num mt-1 text-[13px] text-fg-3">
        {money(data.bank_after)} left in the bank · {data.free_transfers_left ?? 0} free{" "}
        {data.free_transfers_left === 1 ? "transfer" : "transfers"} left
      </p>
      <div className="mt-5 grid grid-cols-3 gap-2">
        {cols.map(([k, label, v, sub]) => (
          <div key={k} className={`rounded-2xl px-3 py-3.5 ${k === "yours" ? "bg-grass-soft" : "bg-raised"}`}>
            <p className="text-[12px] leading-tight text-fg-2">{label}</p>
            <p className={`num mt-2 text-[24px] leading-none font-semibold tracking-[-0.02em] sm:text-[30px] ${v === best ? "text-grass-ink" : ""}`}>
              {f1(v)}
            </p>
            <p className="mt-1.5 text-[11px] leading-tight text-fg-3">{sub}</p>
          </div>
        ))}
      </div>
      {data.summary && <p className="mt-4 text-[15px] leading-relaxed text-fg-2">{data.summary}</p>}
      {ck && isNum(data.p_beats_hold) && (
        <p className="mt-3 flex items-center gap-2 text-[13px] text-fg-2">
          <ConfDots k={ck} />
          {data.p_beats_hold >= 0.5 ? (
            <span>
              How sure: <span className="font-medium text-fg">{CONF_LABEL[ck]}</span>. Your transfers beat no transfers in{" "}
              {pct(data.p_beats_hold)} of tests.
            </span>
          ) : (
            <span>
              <span className="font-medium text-fg">No transfers is safer</span> ({CONF_LABEL[ck].toLowerCase()}). Your transfers win
              in only {pct(data.p_beats_hold)} of tests.
            </span>
          )}
        </p>
      )}
      {win && (
        <div className="mt-5">
          <h3 className="text-[13px] text-fg-3">Chance of winning your league</h3>
          <dl className="num mt-1 grid grid-cols-3 gap-2 text-[15px]">
            {(["yours", "hold", "advisor"] as const).map((k, i) => (
              <div key={k}>
                <dt className="text-[12px] text-fg-3">{cols[i][1]}</dt>
                <dd className="font-semibold">{pct(win[k])}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}
      {L && L.xi?.length > 0 && (
        <div className="mt-6">
          <h3 className="text-[13px] text-fg-3">
            Best line-up{isNum(g) ? ` for GW${g}` : ""}: {f1(L.expected_points)} expected points
          </h3>
          {data.captain && (
            <p className="mt-2 flex items-center gap-2 text-[15px]">
              <span className="grid size-5 place-items-center rounded-full bg-fg text-[10px] font-bold text-bg">C</span>
              <span className="font-semibold">{data.captain.name}</span>
              <span className="text-fg-3">captain</span>
            </p>
          )}
          <ul className="mt-3 flex flex-wrap gap-1.5">
            {L.xi.map((p) => (
              <li key={p.id} className="inline-flex items-center gap-1.5 rounded-full bg-raised py-1 pr-3 pl-1 text-[13px]">
                <Token team={p.team} size={18} label={false} />
                {p.name}
              </li>
            ))}
          </ul>
          {(L.bench || []).length > 0 && (
            <p className="mt-2 text-[13px] text-fg-3">Bench: {(L.bench || []).map((p) => p.name).join(", ")}</p>
          )}
        </div>
      )}
      <div className="mt-6">
        <h3 className="text-[13px] text-fg-3">What the advisor would do next from this squad</h3>
        {(data.next || []).length ? (
          <ul className="mt-1">
            {(data.next || []).map((w, wi) => (
              <li key={w.gameweek ?? wi} className="border-t border-line py-2.5 text-[15px] text-fg-2 first:border-t-0">
                <span className="num mr-2 font-semibold">GW{w.gameweek}</span>
                {w.chip && <span>Play {w.chip}. </span>}
                {(w.moves || []).map((x, k) => (
                  <span key={k}>
                    {k > 0 && "; "}
                    {x.out?.name} → <span className="text-fg">{x.in?.name}</span>
                  </span>
                ))}
                {isNum(w.hits) && w.hits > 0 && <span className="text-red"> (−{w.hits * 4})</span>}
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-[15px] text-fg-2">No more moves planned over {horizonLabel(data.horizon) || "the next few weeks"}.</p>
        )}
      </div>
    </m.div>
  );
}

function planNote(n?: number) {
  if (!n) return "no transfers now";
  return `${n} ${n === 1 ? "transfer" : "transfers"} now`;
}
