import type { ReactNode } from "react";
import { Fixtures } from "../components/Fixtures";
import { Md } from "../components/Md";
import type { SheetSpec } from "../components/Sheet";
import { Token } from "../components/Token";
import { ConfDots, Meter, Prose, Rise, Stat, StatusDot, SubHead } from "../components/ui";
import {
  availability,
  capitalise,
  CONF_LABEL,
  confKey,
  inTen,
  moneyText,
  dateTime,
  f1,
  horizonLabel,
  isNum,
  money,
  pct,
  safeUrl,
  signed,
} from "../lib/format";
import type { Advice, Chip, Move, Player } from "../lib/types";

const POS_NAME: Record<string, string> = { GKP: "Goalkeeper", DEF: "Defender", MID: "Midfielder", FWD: "Forward" };

function News({ p }: { p: Player }) {
  const a = availability(p);
  const src = safeUrl(p.team_news_source);
  if (!a && !p.team_news) return null;
  return (
    <div className="mt-6 space-y-3">
      {a && (
        <p className={`flex items-start gap-3 rounded-2xl px-4 py-3 text-[15px] ${a.kind === "bad" ? "bg-red-soft" : "bg-amber-soft"}`}>
          <StatusDot p={p} className="mt-2" />
          <span className="text-fg">{a.text}</span>
        </p>
      )}
      {p.team_news && p.team_news !== p.news && (
        <p className="text-[15px] leading-relaxed text-fg-2">
          {p.team_news}{" "}
          {src && (
            <a href={src} target="_blank" rel="noopener noreferrer" className="text-fg underline decoration-line-2 underline-offset-4 hover:decoration-fg">
              Source
            </a>
          )}
        </p>
      )}
    </div>
  );
}

function PlayerHead({ p, sub }: { p: Player; sub?: ReactNode }) {
  return (
    <span className="flex items-center gap-4">
      <Token team={p.team} size={52} />
      <span className="min-w-0">
        <span className="block truncate">{p.name}</span>
        <span className="block text-[15px] font-normal text-fg-2">
          {sub ?? [p.team, p.pos && (POS_NAME[p.pos] || p.pos)].filter(Boolean).join(" · ")}
        </span>
      </span>
    </span>
  );
}

export function playerSheet(p: Player, d: Advice, extra?: { cv?: "C" | "V"; ownership?: number; ownershipLabel?: string }): SheetSpec {
  const hz = horizonLabel(d.horizon);
  return {
    title: p.name,
    head: <PlayerHead p={p} />,
    body: (
      <>
        <div className="grid grid-cols-2 gap-3">
          <div className="rounded-2xl bg-raised px-4 py-4">
            <div className="num text-[34px] leading-none font-semibold tracking-tight">{f1(p.xp_next)}</div>
            <div className="mt-2 text-[13px] text-fg-2">Expected points, next GW{extra?.cv === "C" ? " (doubled as captain)" : ""}</div>
          </div>
          <div className="rounded-2xl bg-raised px-4 py-4">
            <div className="num text-[34px] leading-none font-semibold tracking-tight">{f1(p.xp_horizon)}</div>
            <div className="mt-2 text-[13px] text-fg-2">Expected points, {hz}</div>
          </div>
        </div>
        <SubHead>Next fixtures</SubHead>
        <Fixtures fixtures={p.fixtures} />
        <p className="mt-2 text-[13px] text-fg-3">H home · A away</p>
        <dl className="mt-6">
          <Stat
            k="Price"
            v={
              <span className="inline-flex items-center gap-2">
                <Rise dir={p.price_move} />
                {money(p.price)}
              </span>
            }
          />
          {isNum(p.sell_price) && p.sell_price !== p.price && <Stat k="Sells for" v={money(p.sell_price)} />}
          {isNum(p.p_start) && <Stat k="Chance to start" v={pct(p.p_start)} />}
          {isNum(p.p_goal) && <Stat k="Chance to score" v={pct(p.p_goal)} />}
          {isNum(p.p_assist) && <Stat k="Chance to assist" v={pct(p.p_assist)} />}
          {isNum(p.elite_own) && <Stat k="Top managers who own" v={pct(p.elite_own)} />}
          {isNum(p.elite_captain) && p.elite_captain > 0 && <Stat k="Top managers who captain" v={pct(p.elite_captain)} />}
          {isNum(extra?.ownership) && <Stat k={extra?.ownershipLabel || "Rivals own"} v={pct(extra?.ownership)} />}
        </dl>
        <News p={p} />
      </>
    ),
  };
}

function Side({ p, out }: { p: Player; out: boolean }) {
  return (
    <div className="min-w-0">
      <div className="flex items-center gap-3">
        <Token team={p.team} size={36} dim={out} />
        <span className={`text-[15px] font-medium ${out ? "text-red" : "text-grass-ink"}`}>{out ? "Out" : "In"}</span>
        <StatusDot p={p} />
      </div>
    </div>
  );
}

function CompareRow({ k, a, b }: { k: string; a: ReactNode; b: ReactNode }) {
  return (
    <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-3 border-t border-line py-3">
      <div className="num text-[17px] font-semibold">{a}</div>
      <div className="text-center text-[13px] text-fg-3">{k}</div>
      <div className="num text-right text-[17px] font-semibold">{b}</div>
    </div>
  );
}

/** Money, confidence and the plain points difference: what the plan weighed for this move. */
function PlanFacts({ m }: { m: Move }) {
  const cash = moneyText(m.money);
  const ck = confKey(m.confidence?.level);
  const odds = inTen(m.confidence?.p_beats_hold);
  const plain = isNum(m.xp_difference) && isNum(m.gain) && Math.abs(m.xp_difference - m.gain) >= 0.1 ? m.xp_difference : null;
  if (!cash && !ck && plain === null) return null;
  return (
    <div className="mt-6 space-y-3">
      {(cash || ck) && (
        <div className="grid grid-cols-2 gap-3">
          {cash && (
            <div className="rounded-2xl bg-raised px-4 py-4">
              <div className="text-[13px] text-fg-3">Money</div>
              <div className="num mt-1 text-[19px] font-semibold">{capitalise(cash)}</div>
              <div className="mt-1 text-[13px] text-fg-2">
                {isNum(m.money) && Math.abs(m.money) >= 0.05 ? (m.money > 0 ? "Added to your bank" : "Taken from your bank") : "Same price"}
              </div>
            </div>
          )}
          {ck && (
            <div className="rounded-2xl bg-raised px-4 py-4">
              <div className="text-[13px] text-fg-3">How sure the plan is</div>
              <div className="mt-1 flex items-center gap-2 text-[19px] font-semibold">
                <ConfDots k={ck} />
                {CONF_LABEL[ck]}
              </div>
              {odds && <div className="mt-1 text-[13px] text-fg-2">Beats saving the transfer in {odds} simulations</div>}
            </div>
          )}
        </div>
      )}
      {plain !== null && (
        <p className="text-[13px] leading-relaxed text-fg-3">
          Plain difference in expected points: <span className="num font-medium text-fg-2">{signed(plain)}</span>. The plan
          weighs nearer weeks more and counts whether he would start or sit on your bench.
        </p>
      )}
    </div>
  );
}

export function transferSheet(m: Move, d: Advice): SheetSpec {
  const o = m.out!;
  const n = m.in!;
  const hz = horizonLabel(d.horizon);
  const timing = (d.timing || []).filter((t) => t.includes(n.name));
  const tr = d.transfers || {};
  return {
    title: `${o.name} to ${n.name}`,
    head: (
      <span className="flex flex-wrap items-baseline gap-x-2">
        <span className="text-fg-2">{o.name}</span>
        <span aria-hidden="true" className="text-fg-3">→</span>
        <span>{n.name}</span>
      </span>
    ),
    body: (
      <>
        <div className="flex items-end gap-3">
          <span className={`num text-[64px] leading-[0.9] font-semibold tracking-[-0.03em] ${isNum(m.gain) && m.gain < 0 ? "text-red" : "text-grass-ink"}`}>
            {signed(m.gain)}
          </span>
          <span className="pb-1.5 text-[15px] text-fg-2">expected points gained, <span className="whitespace-nowrap">{hz}</span>, as the plan counts them</span>
        </div>
        <PlanFacts m={m} />

        <div className="mt-8 grid grid-cols-2 gap-4">
          <Side p={o} out />
          <div className="justify-self-end">
            <Side p={n} out={false} />
          </div>
        </div>
        <div className="mt-4">
          <CompareRow k="Price (sell / buy)" a={money(isNum(o.sell_price) ? o.sell_price : o.price)} b={<span className="inline-flex items-center gap-1.5"><Rise dir={n.price_move} />{money(n.price)}</span>} />
          <CompareRow k="Exp. points next GW" a={f1(o.xp_next)} b={f1(n.xp_next)} />
          <CompareRow k={`Exp. points ${hz}`} a={f1(o.xp_horizon)} b={f1(n.xp_horizon)} />
          {(isNum(o.p_goal) || isNum(n.p_goal)) && <CompareRow k="Chance to score" a={pct(o.p_goal)} b={pct(n.p_goal)} />}
          {(isNum(o.elite_own) || isNum(n.elite_own)) && <CompareRow k="Top managers own" a={pct(o.elite_own)} b={pct(n.elite_own)} />}
          <div className="grid grid-cols-2 gap-3 border-t border-line pt-4">
            <Fixtures fixtures={o.fixtures} size="xs" max={3} />
            <div className="flex justify-end">
              <Fixtures fixtures={n.fixtures} size="xs" max={3} />
            </div>
          </div>
          <p className="mt-2 text-center text-[13px] text-fg-3">Next fixtures: H home · A away</p>
        </div>

        {(m.why_out || m.why_in) && (
          <>
            <SubHead>Why</SubHead>
            <Prose>
              {m.why_out && (
                <p>
                  <span className="font-medium text-fg">Sell {o.name}.</span> <Md text={capitalise(m.why_out)} />
                </p>
              )}
              {m.why_in && (
                <p>
                  <span className="font-medium text-fg">Buy {n.name}.</span> <Md text={m.why_in} />
                </p>
              )}
            </Prose>
          </>
        )}
        {timing.length > 0 && (
          <div className="mt-6 space-y-2">
            {timing.map((t, i) => (
              <p key={i} className="flex gap-3 rounded-2xl bg-amber-soft px-4 py-3 text-[15px] leading-relaxed text-fg">
                <span className="pt-1"><Rise dir="rise" /></span>
                <span><Md text={t} /></span>
              </p>
            ))}
          </div>
        )}
        {isNum(tr.hits) && tr.hits > 0 && tr.hit_note && (
          <p className="mt-6 rounded-2xl bg-red-soft px-4 py-3 text-[15px] text-fg">
            <Md text={tr.hit_note} />
          </p>
        )}
        <News p={o} />
        <News p={n} />
      </>
    ),
  };
}

export function captainSheet(d: Advice): SheetSpec {
  const c = d.captain!;
  const p = c.captain!;
  const mult = isNum(c.multiplier) ? c.multiplier : 2;
  const opts = c.options || [];
  const max = Math.max(1, ...opts.map((o) => (isNum(o.xp_next) ? o.xp_next : 0)));
  return {
    title: `Captain ${p.name}`,
    head: <PlayerHead p={p} sub={mult === 3 ? "Triple captain" : "Captain"} />,
    body: (
      <>
        <div className="flex items-end gap-3">
          <span className="num text-[64px] leading-[0.9] font-semibold tracking-[-0.03em]">{f1(isNum(p.xp_next) ? p.xp_next * mult : null)}</span>
          <span className="pb-1.5 text-[15px] text-fg-2">
            expected points ({f1(p.xp_next)} × {mult} as captain)
          </span>
        </div>
        <div className="mt-6">
          <Fixtures fixtures={p.fixtures} max={3} />
          <p className="mt-2 text-[13px] text-fg-3">Next fixtures: H home · A away</p>
        </div>
        {c.vice && (
          <div className="mt-8 flex items-center gap-3 rounded-2xl bg-raised px-4 py-3">
            <span className="grid size-6 place-items-center rounded-full bg-fg-2 text-[11px] font-bold text-bg">V</span>
            <Token team={c.vice.team} size={28} label={false} />
            <span className="flex-1 font-medium">
              {c.vice.name}
              <span className="block text-[13px] font-normal text-fg-3">Vice-captain, the backup captain</span>
            </span>
            <span className="num font-semibold">{f1(c.vice.xp_next)}</span>
          </div>
        )}
        {opts.length > 0 && (
          <>
            <SubHead>Captain options</SubHead>
            <p className="-mt-1 mb-2 text-[13px] text-fg-3">Expected points next GW, before doubling</p>
            <ol>
              {opts.map((o, i) => (
                <li key={o.id} className="flex items-center gap-3 border-t border-line py-3 first:border-t-0">
                  <span className="num w-4 text-[13px] text-fg-3">{i + 1}</span>
                  <Token team={o.team} size={28} label={false} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">{o.name}</span>
                    <Meter value={(o.xp_next || 0) / max} className="mt-2 w-full max-w-40" delay={i * 0.05} />
                  </span>
                  <span className="num text-[17px] font-semibold">{f1(o.xp_next)}</span>
                </li>
              ))}
            </ol>
          </>
        )}
        {(c.note || c.reason_league) && (
          <>
            <SubHead>Why</SubHead>
            <Prose>
              {c.reason_league && (
                <p>
                  <Md text={c.reason_league} />
                </p>
              )}
              {c.note && (
                <p>
                  <Md text={c.note} />
                </p>
              )}
            </Prose>
          </>
        )}
      </>
    ),
  };
}

export function chipsSheet(d: Advice): SheetSpec {
  const list = d.chips || [];
  return {
    title: "Chips",
    body: (
      <ul className="space-y-3">
        {list.map((c: Chip) => (
          <li key={c.chip} className={`rounded-3xl px-5 py-5 ${c.play_now ? "bg-grass-soft" : "bg-raised"}`}>
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-[19px] font-semibold">{c.name || c.chip}</span>
              <span className={`text-sm font-medium ${c.play_now ? "text-grass-ink" : "text-fg-2"}`}>
                {c.play_now ? "Play now" : "Save for later"}
              </span>
            </div>
            {isNum(c.expires_gw) && <div className="mt-0.5 text-[13px] text-fg-3">Use by GW{c.expires_gw}</div>}
            {c.reason && (
              <p className="mt-3 text-[15px] leading-relaxed text-fg-2">
                <Md text={c.reason} />
              </p>
            )}
          </li>
        ))}
      </ul>
    ),
  };
}

export function timingSheet(d: Advice): SheetSpec {
  return {
    title: "When to transfer",
    body: (
      <ul className="space-y-3">
        {(d.timing || []).map((t, i) => (
          <li key={i} className={`flex gap-3 rounded-2xl px-4 py-3 text-[15px] leading-relaxed ${/\brise\b/i.test(t) ? "bg-amber-soft" : "bg-raised"}`}>
            <span className="pt-1">{/\brise\b/i.test(t) && <Rise dir="rise" />}</span>
            <span>
              <Md text={t} />
            </span>
          </li>
        ))}
      </ul>
    ),
  };
}

export function hitSheet(d: Advice): SheetSpec {
  const tr = d.transfers || {};
  return {
    title: "Points hit for extra transfers",
    body: (
      <Prose>
        <p>
          <Md text={tr.hit_note || `This plan costs ${tr.hit_cost} points in hits.`} />
        </p>
      </Prose>
    ),
  };
}

const SOURCE_LABEL: Record<string, string> = {
  odds: "Bookmaker odds",
  news: "Team news",
  elite: "Elite managers",
  creators: "Creator videos",
};

export function aboutSheet(d: Advice | null): SheetSpec {
  const notes = (d?.notes || []).filter(Boolean);
  const s = d?.sources || {};
  const srcs = Object.keys(SOURCE_LABEL)
    .map((k) => [SOURCE_LABEL[k], Date.parse(s[k])] as const)
    .filter(([, t]) => Number.isFinite(t));
  const n = d?.horizon?.length || 5;
  return {
    title: "How this works",
    body: (
      <>
        {notes.length > 0 && (
          <div className="mb-8 space-y-3">
            {notes.map((x, i) => (
              <p key={i} className="rounded-2xl bg-raised px-4 py-3 text-[15px] leading-relaxed">
                <Md text={x} />
              </p>
            ))}
          </div>
        )}
        <Prose>
          <p>
            Every player gets <span className="font-medium text-fg">expected points (xP)</span> for each upcoming
            gameweek, from expected goals and assists, usual minutes and fixture difficulty.
          </p>
          <p>
            An optimiser then picks the transfers, lineup, captain and chips that add the most xP over the next {n}{" "}
            gameweeks, within your budget and FPL's rules. A −4 hit is only suggested when it pays for itself.
          </p>
          <p>These are estimates, not promises. Check team news before the deadline.</p>
        </Prose>
        <SubHead>Updated</SubHead>
        <dl>
          <Stat k="FPL data" v={<span className="text-[15px] font-medium">Live</span>} />
          {srcs.map(([k, t]) => (
            <Stat key={k} k={k} v={<span className="text-[15px] font-medium">{dateTime.format(t)}</span>} />
          ))}
        </dl>
        <p className="mt-8 text-[13px] leading-relaxed text-fg-3">
          Unofficial and open source. Not affiliated with the Premier League or Fantasy Premier League.
        </p>
      </>
    ),
  };
}
