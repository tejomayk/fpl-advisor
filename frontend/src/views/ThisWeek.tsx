import { m } from "motion/react";
import type { ReactNode } from "react";
import { CountUp } from "../components/CountUp";
import { useSheet } from "../components/Sheet";
import { Token } from "../components/Token";
import { Md } from "../components/Md";
import {
  Chevron,
  ConfDots,
  confLegend,
  DotMark,
  item,
  Legend,
  list,
  Rise,
  RowButton,
  Section,
  StatusDot,
  Tip,
} from "../components/ui";
import { availability, CONF_LABEL, confKey, f1, horizonLabel, isNum, money, moneyText, signed } from "../lib/format";
import type { Advice, Move, PlanWeek, Player } from "../lib/types";
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
          <p className="mt-4 text-[17px] text-fg">Extra expected points this week from these moves</p>
          <p className="num mt-2 flex flex-wrap items-center gap-x-2 text-[15px] text-fg-2">
            <span>{f1(now)} now</span>
            <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" className="text-fg-3">
              <path d="M4 12h15m-5-5 5 5-5 5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            <span className="sr-only">to</span>
            <span className="font-semibold text-fg">{f1(after)}</span>
            <span>after</span>
          </p>
        </>
      ) : (
        after !== null && (
          <p className="mt-3 flex items-baseline gap-3">
            <span className="num text-[clamp(72px,18vw,128px)] leading-[0.88] font-semibold tracking-[-0.03em]">
              <CountUp value={after} />
            </span>
            <span className="text-[17px] text-fg-2">expected points this week</span>
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
  const cash = moneyText(mv.money);
  const ck = confKey(mv.confidence?.level);
  const label = [`Sell ${o.name}, buy ${n.name}`, cash, ck && `confidence: ${CONF_LABEL[ck]}`].filter(Boolean).join(", ");
  return (
    <RowButton onClick={() => show(transferSheet(mv, d))} label={label} className="py-5">
      <span className="flex min-w-0 items-stretch gap-3">
        <Tip label="Out, then in" className="w-[30px] shrink-0">
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
            <span className="w-7 shrink-0 text-[12px] font-semibold text-red">Out</span>
            <span className="truncate text-[17px] text-fg-2">{o.name}</span>
            <span className="text-[12px] font-medium text-fg-3">{o.team}</span>
            <StatusDot p={o} className="self-center" />
          </span>
          <span className="flex min-w-0 items-baseline gap-2">
            <span className="w-7 shrink-0 text-[12px] font-semibold text-grass-ink">In</span>
            <span className="truncate text-[17px] font-semibold">{n.name}</span>
            <span className="text-[12px] font-medium text-fg-3">{n.team}</span>
            <span className="self-center">
              <Rise dir={n.price_move} />
            </span>
          </span>
        </span>
      </span>
      <span className="ml-auto flex shrink-0 items-center gap-2">
        <span className="flex flex-col items-end gap-1.5">
          {isNum(mv.gain) && (
            <span className={`num text-[30px] leading-none font-semibold tracking-[-0.02em] ${mv.gain < 0 ? "text-red" : "text-grass-ink"}`}>
              <CountUp value={mv.gain} sign delay={0.15 + i * 0.06} />
            </span>
          )}
          {(cash || ck) && (
            <span className="num flex flex-col items-end gap-1 text-[13px] whitespace-nowrap text-fg-2 sm:flex-row sm:items-center sm:gap-2">
              {ck && <ConfDots k={ck} />}
              {cash}
            </span>
          )}
        </span>
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
      <Section
        title="Your squad"
        sub={`Price, then expected points over ${horizonLabel(d.horizon)}`}
        meta={isNum(tr.bank_after) ? `${money(tr.bank_after)} left in the bank` : null}
      >
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
      {isNum(ft) && <span>{ft} free {ft === 1 ? "transfer" : "transfers"}</span>}
      {moves.length > 0 && isNum(tr.bank_after) && <span> · {money(tr.bank_after)} left</span>}
    </span>
  );
  const timing = (d.timing || []).filter(Boolean);
  const rises = moves.filter((m) => m.in?.price_move === "rise").length;
  const marks: [ReactNode, string][] = [];
  const kinds = new Set(moves.flatMap((x) => [availability(x.out)?.kind, availability(x.in)?.kind]));
  if (kinds.has("bad")) marks.push([<DotMark tone="bad" />, "Injured or out"]);
  if (kinds.has("warn")) marks.push([<DotMark tone="warn" />, "Doubtful"]);
  if (moves.some((x) => x.in?.price_move === "rise")) marks.push([<Rise dir="rise" />, "Price may rise tonight"]);
  const confs = confLegend(moves.map((x) => confKey(x.confidence?.level)));
  const overall = confKey(tr.confidence?.level);
  const later = (tr.pencilled || []).filter((w) => (w.moves || []).some((x) => x.out && x.in) || w.chip);
  return (
    <Section
      title="Transfers"
      sub={moves.length ? `Expected points gained over ${horizonLabel(d.horizon) || "the next few gameweeks"}, as the plan counts them` : undefined}
      meta={meta}
    >
      {moves.length ? (
        <m.ul variants={list} initial="hidden" animate="show">
          {moves.map((mv, i) => (
            <TransferRow key={`${mv.out!.id}-${mv.in!.id}`} mv={mv} d={d} i={i} />
          ))}
        </m.ul>
      ) : (
        <Hold d={d} />
      )}
      {moves.length > 0 && isNum(tr.value_of_moves) && (
        <m.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ type: "spring", stiffness: 220, damping: 28, delay: 0.3 }}
          className="mt-3 flex items-center gap-4 rounded-2xl bg-raised px-4 py-3.5"
        >
          <span className={`num text-[22px] font-semibold tracking-[-0.01em] ${tr.value_of_moves < 0 ? "text-red" : "text-grass-ink"}`}>
            {signed(tr.value_of_moves)}
          </span>
          <span className="min-w-0 flex-1 text-[15px] leading-snug text-fg-2">
            points for making {moves.length === 1 ? "this move" : "these moves"} now instead of saving your transfers
          </span>
          {overall && (
            <span className="flex shrink-0 items-center gap-2 text-[13px] text-fg-2">
              <ConfDots k={overall} />
              {CONF_LABEL[overall]}
            </span>
          )}
        </m.div>
      )}
      {confs.length > 0 && (
        <Legend items={[[<span className="text-fg-2">How sure the plan is:</span>, ""], ...confs]} className="mt-5" />
      )}
      <Legend items={marks} className={confs.length ? "mt-2" : "mt-4"} />
      {(hits > 0 || timing.length > 0) && (
        <div className="mt-4 flex flex-wrap gap-2">
          {hits > 0 && (
            <m.button
              type="button"
              whileTap={{ scale: 0.97 }}
              onClick={() => show(hitSheet(d))}
              className="num inline-flex h-9 items-center gap-2 rounded-full bg-red-soft px-4 text-sm font-semibold text-red"
            >
              −{isNum(tr.hit_cost) ? tr.hit_cost : hits * 4} points for extra transfers
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
              {rises ? (rises === 1 ? "Buy before tonight's price rise" : `${rises} prices may rise tonight`) : "When to make transfers"}
            </m.button>
          )}
        </div>
      )}
      {later.length > 0 && <Later weeks={later} />}
    </Section>
  );
}

/** No move this week: say so plainly, with the plan's own reason. */
function Hold({ d }: { d: Advice }) {
  const tr = d.transfers || {};
  const ft = isNum(tr.free_transfers) ? tr.free_transfers : d.team?.free_transfers;
  const why = (tr.headline || "").replace(/^\*\*No transfers this week\.\*\*\s*/i, "").trim();
  const ck = confKey(tr.confidence?.level);
  return (
    <m.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="py-5">
      <p className="text-[22px] font-semibold tracking-[-0.01em]">
        Save your free {ft === 1 ? "transfer" : "transfers"}
      </p>
      {why && (
        <p className="mt-2 text-[15px] leading-relaxed text-fg-2">
          <Md text={why} />
        </p>
      )}
      {ck && (
        <p className="mt-3 flex items-center gap-2 text-[13px] text-fg-2">
          <ConfDots k={ck} />
          <span>
            How sure the plan is: <span className="font-medium text-fg">{CONF_LABEL[ck]}</span>
          </span>
        </p>
      )}
    </m.div>
  );
}

/** Moves the plan has pencilled in for later gameweeks: context, not instructions for now. */
function Later({ weeks }: { weeks: PlanWeek[] }) {
  return (
    <m.section
      aria-labelledby="later-title"
      initial={{ opacity: 0, y: 12 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true }}
      transition={{ type: "spring", stiffness: 200, damping: 28 }}
      className="mt-12 rounded-[24px] px-5 pt-5 pb-3 ring-1 ring-line ring-inset"
    >
      <h3 id="later-title" className="text-[15px] font-medium text-fg-2">
        Later in the plan
      </h3>
      <p className="mt-0.5 text-[13px] text-fg-3">Planned for later weeks; may change</p>
      <m.ol variants={list} initial="hidden" whileInView="show" viewport={{ once: true }} className="mt-3">
        {weeks.map((w, wi) => {
          const mv = (w.moves || []).filter((x) => x.out && x.in);
          const ft = w.free_transfers;
          const hit = isNum(w.hits) && w.hits > 0 ? `, −${w.hits * 4} points` : "";
          return (
            <m.li key={w.gameweek ?? wi} variants={item} className="border-t border-line py-3 first:border-t-0">
              <div className="flex items-baseline justify-between gap-3 text-[13px]">
                <span className="num font-semibold text-fg-2">GW{w.gameweek}</span>
                {mv.length > 0 && isNum(ft) && (
                  <span className="num text-fg-3">
                    {ft} free {ft === 1 ? "transfer" : "transfers"}
                    {hit}
                  </span>
                )}
              </div>
              {w.chip && <p className="mt-1.5 text-[15px] text-fg-2">Play {w.chip}</p>}
              {mv.map((x) => (
                <div key={`${x.out!.id}-${x.in!.id}`} className="mt-2 flex items-center gap-3">
                  <span aria-hidden="true" className="flex shrink-0 items-center gap-1">
                    <Token team={x.out!.team} size={20} dim label={false} />
                    <Token team={x.in!.team} size={20} label={false} />
                  </span>
                  <span className="min-w-0 flex-1 truncate text-[15px] text-fg-2">
                    {x.out!.name} <span aria-hidden="true" className="text-fg-3">→</span>
                    <span className="sr-only">to</span> <span className="text-fg">{x.in!.name}</span>
                  </span>
                  <span className="num shrink-0 text-right text-[13px] text-fg-3">
                    {isNum(x.gain) && <span className="font-semibold text-fg-2">{signed(x.gain)} pts</span>}
                    {moneyText(x.money) && <span className="block">{moneyText(x.money)}</span>}
                  </span>
                </div>
              ))}
            </m.li>
          );
        })}
      </m.ol>
    </m.section>
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
    <Section
      title={mult === 3 ? "Triple captain" : "Captain"}
      sub={`Scores ${mult === 3 ? "triple" : "double"}. The vice-captain is the backup.`}
    >
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
                Vice-captain: {c.vice.name}
              </span>
            )}
          </span>
          <span className="flex items-center gap-2">
            {isNum(p.xp_next) && (
              <span className="text-right">
                <span className="num block text-[30px] leading-none font-semibold tracking-[-0.02em]">
                  <CountUp value={p.xp_next * mult} delay={0.2} />
                </span>
                <span className="mt-1 block text-[12px] text-fg-3">exp. points</span>
              </span>
            )}
            <Chevron />
          </span>
        </RowButton>
      </m.ul>
      {c.reason_league && (
        <m.p
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.3 }}
          className="mt-2 flex gap-3 border-t border-line pt-4 text-[15px] leading-relaxed text-fg-2"
        >
          <TrophyIcon />
          <span>
            <Md text={c.reason_league} />
          </span>
        </m.p>
      )}
    </Section>
  );
}

function TrophyIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" className="mt-1 shrink-0 text-fg-3">
      <path
        d="M8 4h8v5a4 4 0 0 1-8 0V4zM8 6H5v1a3 3 0 0 0 3 3M16 6h3v1a3 3 0 0 1-3 3M12 13v4M9 20h6"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function Chips({ d }: { d: Advice }) {
  const show = useSheet();
  const chips = d.chips || [];
  if (!chips.length) return null;
  const now = chips.find((c) => c.play_now);
  return (
    <Section title="Chips" sub="One-off boosts, each used once">
      <m.ul variants={list} initial="hidden" animate="show">
        <RowButton onClick={() => show(chipsSheet(d))} label={now ? `Play ${now.name}` : "Save your chips for later"} className="py-5">
          {now ? (
            <span className="flex-1 text-[22px] font-semibold tracking-[-0.01em]">
              Play <span className="text-grass-ink">{now.name || now.chip}</span>
            </span>
          ) : (
            <span className="min-w-0 flex-1">
              <span className="block text-[22px] leading-tight font-semibold tracking-[-0.01em]">
                Save {chips.length > 1 ? "them" : "it"} for later
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
        <span>Biggest single gain</span>
        <span className="num">expected points, {hz}</span>
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
        <span className="block text-[15px] font-medium opacity-75">Play this chip now</span>
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
