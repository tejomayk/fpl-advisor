import { m } from "motion/react";
import { CountUp } from "../components/CountUp";
import { useSheet } from "../components/Sheet";
import { Token } from "../components/Token";
import { Chevron, list, Rise, RowButton, Section, StatusDot, Tip } from "../components/ui";
import { f1, horizonLabel, isNum, money, signed } from "../lib/format";
import type { Advice, Move, Player } from "../lib/types";
import {
  captainSheet,
  chipsSheet,
  hitSheet,
  playerSheet,
  timingSheet,
  transferSheet,
} from "../sheets/sheets";

function Hero({ d, demo }: { d: Advice; demo: boolean }) {
  const L = d.lineup;
  const C = L?.current;
  const now = isNum(C?.expected_points) ? C!.expected_points! : null;
  const after = isNum(L?.expected_points) ? L!.expected_points! : null;
  const gain = now !== null && after !== null ? after - now : null;
  const showGain = gain !== null && Math.abs(gain) >= 0.05;
  return (
    <m.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ type: "spring", stiffness: 200, damping: 28 }}
      className="pt-10 md:pt-20 desk:pt-24"
    >
      <p className="text-[15px] font-medium text-fg-2">
        Gameweek {d.gameweek?.id ?? "–"}
        {demo && <span className="text-fg-3"> · Example team</span>}
      </p>
      {showGain ? (
        <>
          <p className="num mt-3 text-[clamp(72px,18vw,128px)] leading-[0.88] font-semibold tracking-[-0.03em] text-grass-ink">
            <CountUp value={gain!} sign />
          </p>
          <p className="num mt-5 flex flex-wrap items-center gap-x-2.5 text-[17px] text-fg-2">
            <span>{f1(now)}</span>
            <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" className="text-fg-3">
              <path d="M4 12h15m-5-5 5 5-5 5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            <span className="sr-only">to</span>
            <span className="font-semibold text-fg">{f1(after)}</span>
            <span>xP this week</span>
          </p>
        </>
      ) : (
        after !== null && (
          <p className="mt-3 flex items-baseline gap-3">
            <span className="num text-[clamp(72px,18vw,128px)] leading-[0.88] font-semibold tracking-[-0.03em]">
              <CountUp value={after} />
            </span>
            <span className="text-[17px] text-fg-2">xP this week</span>
          </p>
        )
      )}
    </m.div>
  );
}

function TransferRow({ mv, d, i }: { mv: Move; d: Advice; i: number }) {
  const show = useSheet();
  const o = mv.out!;
  const n = mv.in!;
  return (
    <RowButton onClick={() => show(transferSheet(mv, d))} label={`Sell ${o.name}, buy ${n.name}`} className="py-5">
      <span className="flex min-w-0 items-stretch gap-3">
        <Tip label="Sell ↓ Buy" className="w-[30px] shrink-0">
        <span aria-hidden="true" className="flex w-[30px] flex-col items-center">
          <Token team={o.team} size={30} dim label={false} />
          <span className="relative my-1 w-px flex-1 bg-line-2">
            <svg viewBox="0 0 10 6" width="9" height="6" className="absolute -bottom-1 left-1/2 -translate-x-1/2 text-fg-3">
              <path d="M1 1l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </span>
          <Token team={n.team} size={30} label={false} />
        </span>
        </Tip>
        <span className="flex min-w-0 flex-col justify-between gap-5 py-[3px]">
          <span className="flex min-w-0 items-baseline gap-2">
            <span className="truncate text-[17px] text-fg-2">{o.name}</span>
            <span className="text-[12px] font-medium text-fg-3">{o.team}</span>
            <StatusDot p={o} className="self-center" />
          </span>
          <span className="flex min-w-0 items-baseline gap-2">
            <span className="truncate text-[17px] font-semibold">{n.name}</span>
            <span className="text-[12px] font-medium text-fg-3">{n.team}</span>
            <span className="self-center">
              <Rise dir={n.price_move} />
            </span>
          </span>
        </span>
      </span>
      <span className="ml-auto flex items-center gap-2">
        {isNum(mv.gain) && (
          <span className={`num text-[30px] font-semibold tracking-[-0.02em] ${mv.gain < 0 ? "text-red" : "text-grass-ink"}`}>
            <CountUp value={mv.gain} sign delay={0.15 + i * 0.06} />
          </span>
        )}
        <Chevron className="transition-transform duration-200 group-hover:translate-x-0.5" />
      </span>
    </RowButton>
  );
}

function PlayerRow({ p, d }: { p: Player; d: Advice }) {
  const show = useSheet();
  return (
    <RowButton onClick={() => show(playerSheet(p, d))} label={p.name} className="py-3.5">
      <Token team={p.team} size={30} label={false} />
      <span className="flex min-w-0 flex-1 items-center gap-2">
        <span className="truncate text-[17px] font-medium">{p.name}</span>
        <StatusDot p={p} />
        <Rise dir={p.price_move} />
      </span>
      <span className="num text-[15px] text-fg-2">{money(p.price)}</span>
      <span className="num w-12 text-right text-[17px] font-semibold">{f1(p.xp_horizon)}</span>
    </RowButton>
  );
}

const POS: [string, string][] = [
  ["GKP", "Goalkeepers"],
  ["DEF", "Defenders"],
  ["MID", "Midfielders"],
  ["FWD", "Forwards"],
];

function Transfers({ d }: { d: Advice }) {
  const show = useSheet();
  const tr = d.transfers;
  if (!tr) return null;
  if (tr.initial) {
    const squad = tr.squad || [];
    return (
      <Section title="Your squad" meta={isNum(tr.bank_after) ? `${money(tr.bank_after)} left` : null}>
        {POS.map(([pos, label]) => {
          const ps = squad.filter((p) => p.pos === pos);
          if (!ps.length) return null;
          return (
            <div key={pos} className="mt-6 first:mt-2">
              <h3 className="mb-1 text-[13px] text-fg-3">{label}</h3>
              <m.ul variants={list} initial="hidden" animate="show">
                {ps.map((p) => (
                  <PlayerRow key={p.id} p={p} d={d} />
                ))}
              </m.ul>
            </div>
          );
        })}
      </Section>
    );
  }
  const moves = (tr.moves || []).filter((m) => m.out && m.in);
  const hits = isNum(tr.hits) ? tr.hits : 0;
  const ft = isNum(tr.free_transfers) ? tr.free_transfers : d.team?.free_transfers;
  const meta = (
    <span className="num">
      {moves.length > 0 && horizonLabel(d.horizon) && <span>{horizonLabel(d.horizon)} · </span>}
      {isNum(ft) && <span>{ft} free</span>}
      {moves.length > 0 && isNum(tr.bank_after) && <span> · {money(tr.bank_after)} left</span>}
    </span>
  );
  const timing = (d.timing || []).filter(Boolean);
  const rises = moves.filter((m) => m.in?.price_move === "rise").length;
  return (
    <Section title="Transfers" meta={meta}>
      {moves.length ? (
        <m.ul variants={list} initial="hidden" animate="show">
          {moves.map((mv, i) => (
            <TransferRow key={`${mv.out!.id}-${mv.in!.id}`} mv={mv} d={d} i={i} />
          ))}
        </m.ul>
      ) : (
        <m.p
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          className="py-5 text-[22px] font-semibold tracking-[-0.01em]"
        >
          Save your transfer
        </m.p>
      )}
      {(hits > 0 || timing.length > 0) && (
        <div className="mt-4 flex flex-wrap gap-2">
          {hits > 0 && (
            <m.button
              type="button"
              whileTap={{ scale: 0.97 }}
              onClick={() => show(hitSheet(d))}
              className="num inline-flex h-9 items-center gap-2 rounded-full bg-red-soft px-4 text-sm font-semibold text-red"
            >
              −{isNum(tr.hit_cost) ? tr.hit_cost : hits * 4} pts hit
            </m.button>
          )}
          {timing.length > 0 && (
            <m.button
              type="button"
              whileTap={{ scale: 0.97 }}
              onClick={() => show(timingSheet(d))}
              className={`inline-flex h-9 items-center gap-2 rounded-full px-4 text-sm font-medium ${
                rises ? "bg-amber-soft text-amber" : "bg-raised text-fg-2"
              }`}
            >
              {rises ? <Rise dir="rise" /> : <ClockIcon />}
              {rises ? (rises === 1 ? "Price rise tonight" : `${rises} price rises tonight`) : "When to transfer"}
            </m.button>
          )}
        </div>
      )}
    </Section>
  );
}

function ClockIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="2" />
      <path d="M12 7v5l3 2" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function Captain({ d }: { d: Advice }) {
  const show = useSheet();
  const c = d.captain;
  const p = c?.captain;
  if (!c || !p) return null;
  const mult = isNum(c.multiplier) ? c.multiplier : 2;
  return (
    <Section title={mult === 3 ? "Triple captain" : "Captain"}>
      <m.ul variants={list} initial="hidden" animate="show">
        <RowButton onClick={() => show(captainSheet(d))} label={`Captain ${p.name}`} className="py-5">
          <span className="relative">
            <Token team={p.team} size={48} />
            <span className="absolute -top-1 -right-1 grid size-5 place-items-center rounded-full bg-fg text-[10px] font-bold text-bg ring-2 ring-bg">
              C
            </span>
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[22px] leading-tight font-semibold tracking-[-0.01em]">{p.name}</span>
            {c.vice && (
              <span className="mt-1 flex items-center gap-2 text-[15px] text-fg-2">
                <span className="grid size-[18px] place-items-center rounded-full bg-raised text-[10px] font-bold text-fg-2">V</span>
                {c.vice.name}
              </span>
            )}
          </span>
          <span className="flex items-center gap-2">
            {isNum(p.xp_next) && (
              <span className="num text-[30px] font-semibold tracking-[-0.02em]">
                <CountUp value={p.xp_next * mult} delay={0.2} />
              </span>
            )}
            <Chevron />
          </span>
        </RowButton>
      </m.ul>
    </Section>
  );
}

function Chips({ d }: { d: Advice }) {
  const show = useSheet();
  const chips = d.chips || [];
  if (!chips.length) return null;
  const now = chips.find((c) => c.play_now);
  return (
    <Section title="Chips">
      <m.ul variants={list} initial="hidden" animate="show">
        <RowButton onClick={() => show(chipsSheet(d))} label={now ? `Play ${now.name}` : "Keep all chips"} className="py-5">
          {now ? (
            <span className="flex-1 text-[22px] font-semibold tracking-[-0.01em]">
              Play <span className="text-grass-ink">{now.name || now.chip}</span>
            </span>
          ) : (
            <span className="min-w-0 flex-1">
              <span className="block text-[22px] leading-tight font-semibold tracking-[-0.01em]">
                Keep {chips.length > 1 ? "all" : chips[0].name || chips[0].chip}
              </span>
              <span className="mt-1 block truncate text-[15px] text-fg-2">{chips.map((c) => c.name || c.chip).join(" · ")}</span>
            </span>
          )}
          <Chevron />
        </RowButton>
      </m.ul>
    </Section>
  );
}

/** Desktop only: the single biggest move, beside the hero, when no chip needs playing. */
function BestMove({ d }: { d: Advice }) {
  const show = useSheet();
  const tr = d.transfers;
  if (!tr || tr.initial || (d.chips || []).some((c) => c.play_now)) return null;
  const moves = (tr.moves || []).filter((x) => x.out && x.in && isNum(x.gain));
  if (!moves.length) return null;
  const best = moves.reduce((a, b) => (b.gain! > a.gain! ? b : a));
  const o = best.out!;
  const n = best.in!;
  const hz = horizonLabel(d.horizon);
  return (
    <m.button
      type="button"
      onClick={() => show(transferSheet(best, d))}
      aria-label={`Best move: sell ${o.name}, buy ${n.name}, ${signed(best.gain)} expected points ${hz}`}
      initial={{ opacity: 0, x: 16 }}
      animate={{ opacity: 1, x: 0 }}
      whileTap={{ scale: 0.985 }}
      transition={{ type: "spring", stiffness: 220, damping: 28, delay: 0.25 }}
      className="group mb-1 hidden w-full rounded-[28px] bg-panel px-7 pt-5 pb-6 text-left ring-1 ring-line transition-colors ring-inset hover:bg-raised desk:block"
    >
      <span className="flex items-baseline justify-between text-[13px] text-fg-3">
        <span>Best move</span>
        <span className="num">{hz}</span>
      </span>
      <span className="mt-4 flex items-center gap-4">
        <span aria-hidden="true" className="flex items-center gap-2">
          <Token team={o.team} size={40} dim />
          <svg viewBox="0 0 24 24" width="16" height="16" className="text-fg-3">
            <path d="M5 12h13m-5-5 5 5-5 5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <Token team={n.team} size={40} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[14px] text-fg-2">{o.name}</span>
          <span className="block truncate text-[19px] leading-tight font-semibold">{n.name}</span>
        </span>
        <span className="num text-[32px] font-semibold tracking-[-0.02em] text-grass-ink">{signed(best.gain)}</span>
      </span>
    </m.button>
  );
}

function PlayNow({ d }: { d: Advice }) {
  const show = useSheet();
  const now = (d.chips || []).find((c) => c.play_now);
  if (!now) return null;
  return (
    <m.button
      type="button"
      onClick={() => show(chipsSheet(d))}
      initial={{ opacity: 0, y: 16, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      whileTap={{ scale: 0.985 }}
      transition={{ type: "spring", stiffness: 220, damping: 26, delay: 0.15 }}
      className="mt-10 flex w-full items-center gap-5 rounded-[28px] bg-grass px-6 py-6 text-left text-on-grass md:mt-14 md:px-8 md:py-7 desk:mt-0 desk:mb-1"
    >
      <span className="min-w-0 flex-1">
        <span className="block text-[15px] font-medium opacity-75">Play now</span>
        <span className="block text-[32px] leading-tight font-semibold tracking-[-0.02em] md:text-[40px]">{now.name || now.chip}</span>
      </span>
      <Chevron className="!text-on-grass opacity-70" />
    </m.button>
  );
}

export function ThisWeek({ d, demo = false }: { d: Advice; demo?: boolean }) {
  return (
    <div className="mx-auto w-full max-w-[640px] px-5 pb-8 md:px-0 desk:max-w-[1120px] desk:px-8">
      <div className="desk:grid desk:grid-cols-[minmax(0,1fr)_380px] desk:items-end desk:gap-x-24">
        <Hero d={d} demo={demo} />
        <div>
          <PlayNow d={d} />
          <BestMove d={d} />
        </div>
      </div>
      <div className="mt-16 md:mt-24 desk:grid desk:grid-cols-[minmax(0,1fr)_380px] desk:gap-x-24">
        <Transfers d={d} />
        <div className="mt-16 space-y-16 md:mt-20 md:space-y-20 desk:mt-0">
          <Captain d={d} />
          <Chips d={d} />
        </div>
      </div>
    </div>
  );
}
