import type { ReactNode } from "react";
import { Fixtures } from "../components/Fixtures";
import { Md } from "../components/Md";
import type { SheetSpec } from "../components/Sheet";
import { Token } from "../components/Token";
import { Meter, Prose, Rise, Stat, StatusDot, SubHead } from "../components/ui";
import {
  availability,
  capitalise,
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
            <div className="mt-2 text-[13px] text-fg-2">xP next{extra?.cv === "C" ? " · captain ×2" : ""}</div>
          </div>
          <div className="rounded-2xl bg-raised px-4 py-4">
            <div className="num text-[34px] leading-none font-semibold tracking-tight">{f1(p.xp_horizon)}</div>
            <div className="mt-2 text-[13px] text-fg-2">xP {hz}</div>
          </div>
        </div>
        <SubHead>Fixtures</SubHead>
        <Fixtures fixtures={p.fixtures} />
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
          {isNum(p.elite_own) && <Stat k="Elite managers own" v={pct(p.elite_own)} />}
          {isNum(p.elite_captain) && p.elite_captain > 0 && <Stat k="Elite managers captain" v={pct(p.elite_captain)} />}
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
          <span className="pb-1.5 text-[15px] text-fg-2">xP {hz}</span>
        </div>

        <div className="mt-8 grid grid-cols-2 gap-4">
          <Side p={o} out />
          <div className="justify-self-end">
            <Side p={n} out={false} />
          </div>
        </div>
        <div className="mt-4">
          <CompareRow k="Price" a={money(isNum(o.sell_price) ? o.sell_price : o.price)} b={<span className="inline-flex items-center gap-1.5"><Rise dir={n.price_move} />{money(n.price)}</span>} />
          <CompareRow k="xP next" a={f1(o.xp_next)} b={f1(n.xp_next)} />
          <CompareRow k={`xP ${hz}`} a={f1(o.xp_horizon)} b={f1(n.xp_horizon)} />
          {(isNum(o.p_goal) || isNum(n.p_goal)) && <CompareRow k="To score" a={pct(o.p_goal)} b={pct(n.p_goal)} />}
          {(isNum(o.elite_own) || isNum(n.elite_own)) && <CompareRow k="Elite own" a={pct(o.elite_own)} b={pct(n.elite_own)} />}
          <div className="grid grid-cols-2 gap-3 border-t border-line pt-4">
            <Fixtures fixtures={o.fixtures} size="xs" max={3} />
            <div className="flex justify-end">
              <Fixtures fixtures={n.fixtures} size="xs" max={3} />
            </div>
          </div>
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
            {f1(p.xp_next)} × {mult}
          </span>
        </div>
        <div className="mt-6">
          <Fixtures fixtures={p.fixtures} max={3} />
        </div>
        {c.vice && (
          <div className="mt-8 flex items-center gap-3 rounded-2xl bg-raised px-4 py-3">
            <span className="grid size-6 place-items-center rounded-full bg-fg-2 text-[11px] font-bold text-bg">V</span>
            <Token team={c.vice.team} size={28} label={false} />
            <span className="flex-1 font-medium">{c.vice.name}</span>
            <span className="num font-semibold">{f1(c.vice.xp_next)}</span>
          </div>
        )}
        {opts.length > 0 && (
          <>
            <SubHead>Options</SubHead>
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
        {c.note && (
          <>
            <SubHead>Why</SubHead>
            <Prose>
              <p>
                <Md text={c.note} />
              </p>
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
                {c.play_now ? "Play now" : "Keep"}
              </span>
            </div>
            {isNum(c.expires_gw) && <div className="mt-0.5 text-[13px] text-fg-3">Until GW{c.expires_gw}</div>}
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
    title: "Points hit",
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
