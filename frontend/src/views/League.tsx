import { m } from "motion/react";
import { useState } from "react";
import { CountUp } from "../components/CountUp";
import { Md } from "../components/Md";
import { useSheet } from "../components/Sheet";
import { Segmented } from "../components/Tabs";
import { Token } from "../components/Token";
import { Chevron, item, list, Meter, Prose, RowButton, Section } from "../components/ui";
import { CHIP_SHORT, clamp01, f1, int, isNum, ordinal, pct } from "../lib/format";
import type { Advice, League as LeagueT, Player } from "../lib/types";
import { playerSheet } from "../sheets/sheets";

function ChipCodes({ chips }: { chips?: string[] }) {
  if (!chips) return null;
  if (!chips.length) return <span className="text-[12px] text-fg-3">No chips</span>;
  return (
    <span className="inline-flex gap-1" aria-label={`Chips left: ${chips.join(", ")}`}>
      {chips.map((c) => (
        <span
          key={c}
          title={c}
          aria-hidden="true"
          className="num rounded-[5px] px-1 text-[10px] leading-4 font-semibold tracking-wide text-fg-2 ring-1 ring-line-2 ring-inset"
        >
          {CHIP_SHORT[c] || c.slice(0, 2).toUpperCase()}
        </span>
      ))}
    </span>
  );
}

function Hero({ L, d }: { L: LeagueT; d: Advice }) {
  const table = L.table || [];
  const total = L.total_managers || L.size;
  const lead = isNum(L.gap_to_leader) && L.gap_to_leader <= 0;
  const me = table.find((r) => r.is_me);
  const second = table.find((r) => !r.is_me);
  const myTotal = isNum(me?.total) ? me!.total! : d.team?.total_points;
  const leadBy = lead && isNum(myTotal) && isNum(second?.total) ? myTotal - second!.total! : null;
  return (
    <div className="pt-10 md:pt-20 desk:pt-24">
      <p className="text-[15px] font-medium text-fg-2">{L.name || "Your league"}</p>
      <div className="mt-3 flex flex-wrap items-end gap-x-10 gap-y-6">
        {isNum(L.rank) && (
          <p className="flex items-start">
            <span className="num text-[clamp(72px,18vw,128px)] leading-[0.88] font-semibold tracking-[-0.03em]">
              <CountUp value={L.rank} decimals={0} />
            </span>
            <span className="num mt-2 ml-1 text-[28px] font-semibold text-fg-2">{ordinal(L.rank)}</span>
            {isNum(total) && <span className="sr-only">of {total}</span>}
          </p>
        )}
        <div className="flex gap-8 pb-2">
          {isNum(total) && (
            <div>
              <div className="num text-[28px] leading-none font-semibold">{int(total)}</div>
              <div className="mt-2 text-[13px] text-fg-3">Managers</div>
            </div>
          )}
          {isNum(L.gap_to_leader) && (
            <div>
              <div className={`num text-[28px] leading-none font-semibold ${lead ? "text-grass-ink" : "text-red"}`}>
                {lead ? (isNum(leadBy) && leadBy > 0 ? `+${int(leadBy)}` : "Top") : `−${int(L.gap_to_leader)}`}
              </div>
              <div className="mt-2 text-[13px] text-fg-3">{lead ? "Clear" : "To 1st"}</div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Standings({ L }: { L: LeagueT }) {
  const table = L.table || [];
  const chipsBy = new Map((L.rival_chips || []).filter((r) => Array.isArray(r?.chips)).map((r) => [r.manager, r.chips]));
  const leader = table.find((r) => r.rank === 1) || table[0];
  const leaderPts = isNum(leader?.total) ? leader!.total! : null;
  const inTable = new Set(table.map((r) => r.manager));
  const leftover = (L.rival_chips || []).filter((r) => r && Array.isArray(r.chips) && !inTable.has(r.manager));
  if (!table.length) return null;
  return (
    <Section title="Standings" meta={<span className="hidden sm:inline">GW · Total · To 1st</span>} className="mt-16 md:mt-24">
      <m.ol variants={list} initial="hidden" animate="show" aria-label="Standings">
        {table.map((r, i) => {
          const gap = i > 0 && isNum(r.rank) && isNum(table[i - 1].rank) && r.rank - table[i - 1].rank > 1;
          const behind = leaderPts !== null && isNum(r.total) ? leaderPts - r.total : null;
          return (
            <m.li key={`${r.rank}-${r.manager}`} variants={item} className="relative">
              {gap && (
                <div aria-hidden="true" className="py-1 pl-10 text-fg-3">
                  ⋯
                </div>
              )}
              {r.is_me && (
                <m.span
                  aria-hidden="true"
                  className="absolute inset-y-0 -inset-x-3 rounded-2xl bg-grass-soft"
                  initial={{ opacity: 0, scaleX: 0.92 }}
                  animate={{ opacity: 1, scaleX: 1 }}
                  transition={{ type: "spring", stiffness: 200, damping: 26, delay: 0.35 + i * 0.06 }}
                />
              )}
              <div
                className={`relative grid grid-cols-[1.75rem_1fr_auto_auto_3.5rem] items-center gap-x-3 py-3.5 sm:gap-x-5 ${
                  r.is_me ? "" : "border-t border-line"
                } ${i === 0 ? "border-t-0" : ""}`}
              >
                <span className={`num text-[15px] ${r.is_me ? "font-semibold text-grass-ink" : "text-fg-3"}`}>{r.rank}</span>
                <span className="min-w-0">
                  <span className="flex items-center gap-2">
                    <span className={`truncate text-[16px] ${r.is_me ? "font-semibold" : "font-medium"}`}>{r.manager}</span>
                    {r.is_me && <span className="sr-only">(you)</span>}
                  </span>
                  <span className="mt-0.5 flex items-center gap-2">
                    <span className="truncate text-[13px] text-fg-3">{r.team_name}</span>
                    {!r.is_me && <ChipCodes chips={chipsBy.get(r.manager)} />}
                  </span>
                </span>
                <span className="num text-right text-[15px] text-fg-2" title="This gameweek">
                  {int(r.event_total)}
                </span>
                <span className="num text-right text-[17px] font-semibold">{int(r.total)}</span>
                <span className={`num text-right text-[15px] ${behind === 0 ? "text-grass-ink" : "text-fg-2"}`}>
                  {behind === null ? "–" : behind === 0 ? "1st" : `−${int(behind)}`}
                </span>
              </div>
            </m.li>
          );
        })}
      </m.ol>
      {leftover.length > 0 && (
        <div className="mt-6 border-t border-line pt-5">
        <h3 className="mb-2 text-[13px] text-fg-3">Others</h3>
        <ul className="space-y-2">
          {leftover.map((r) => (
            <li key={r.manager} className="flex items-center justify-between gap-4 text-[15px]">
              <span className="text-fg-2">{r.manager}</span>
              <ChipCodes chips={r.chips} />
            </li>
          ))}
        </ul>
        </div>
      )}
    </Section>
  );
}

function Target({ L, d }: { L: LeagueT; d: Advice }) {
  const show = useSheet();
  const me = d.team?.total_points;
  if (!isNum(L.target)) return null;
  const onPace = isNum(L.my_average) && isNum(L.required_per_gw) && L.my_average >= L.required_per_gw;
  const max = Math.max(L.required_per_gw || 0, L.my_average || 0, 1) * 1.1;
  return (
    <Section title="To win" className="mt-16 md:mt-20 desk:mt-24">
      <div className="pt-2">
        <div className="flex items-end justify-between gap-4">
          <span className="num text-[48px] leading-none font-semibold tracking-[-0.03em]">
            <CountUp value={L.target} decimals={0} />
          </span>
          {isNum(L.remaining_gws) && (
            <span className="pb-1 text-[15px] text-fg-2">
              <span className="num font-semibold text-fg">{L.remaining_gws}</span> GWs left
            </span>
          )}
        </div>
        {isNum(me) && (
          <>
            <Meter value={me / L.target} className="mt-5 h-2" />
            <div className="num mt-2 flex justify-between text-[13px] text-fg-3">
              <span>{int(me)}</span>
              <span>{int(Math.max(0, L.target - me))} to go</span>
            </div>
          </>
        )}
        {isNum(L.required_per_gw) && (
          <dl className="mt-8 space-y-4">
            <div className="grid grid-cols-[7.5rem_1fr_3rem] items-center gap-4">
              <dt className="text-[15px] text-fg-2">Needed / GW</dt>
              <dd><Meter value={L.required_per_gw / max} tone="fg" /></dd>
              <dd className="num text-right text-[17px] font-semibold">{f1(L.required_per_gw)}</dd>
            </div>
            <div className="grid grid-cols-[7.5rem_1fr_3rem] items-center gap-4">
              <dt className="text-[15px] text-fg-2">Your average</dt>
              <dd><Meter value={(L.my_average || 0) / max} tone={onPace ? "grass" : "amber"} delay={0.1} /></dd>
              <dd className={`num text-right text-[17px] font-semibold ${onPace ? "text-grass-ink" : "text-amber"}`}>{f1(L.my_average)}</dd>
            </div>
          </dl>
        )}
        {L.last_season_best && (
          <p className="mt-6 text-[13px] text-fg-3">
            Last season's best: <span className="num">{int(L.last_season_best.points)}</span> ({L.last_season_best.manager})
          </p>
        )}
      </div>
      {L.strategy && (
        <m.ul variants={list} initial="hidden" animate="show" className="mt-6 border-t border-line">
          <RowButton
            className="py-4"
            onClick={() =>
              show({
                title: "Strategy",
                body: (
                  <Prose>
                    <p>
                      <Md text={L.strategy} />
                    </p>
                  </Prose>
                ),
              })
            }
          >
            <span className="flex-1 text-[17px] font-medium">Strategy</span>
            <Chevron />
          </RowButton>
        </m.ul>
      )}
    </Section>
  );
}

function Ownership({ L, d }: { L: LeagueT; d: Advice }) {
  const show = useSheet();
  const [tab, setTab] = useState<"threats" | "diffs">("threats");
  const total = L.total_managers || L.size;
  const partial = isNum(total) && isNum(L.tracked) && L.tracked < total;
  const rows: Player[] = (tab === "threats" ? L.threats : L.differentials) || [];
  const label = partial ? "Tracked rivals own" : "Rivals own";
  if (!L.threats?.length && !L.differentials?.length) return null;
  return (
    <Section title="Rival ownership" meta={partial ? `${L.tracked} tracked` : null} className="mt-16 md:mt-20">
      <Segmented
        label="Ownership list"
        value={tab}
        onChange={setTab}
        className="mt-2 mb-4"
        items={[
          { id: "threats", label: "Threats" },
          { id: "diffs", label: "Differentials" },
        ]}
      />
      {rows.length ? (
        <m.ul key={tab} variants={list} initial="hidden" animate="show">
          {rows.map((p, i) => (
            <RowButton
              key={p.id}
              className="py-3.5"
              label={`${p.name}, ${label.toLowerCase()} ${pct(p.ownership)}`}
              onClick={() => show(playerSheet(p, d, { ownership: p.ownership, ownershipLabel: label }))}
            >
              <Token team={p.team} size={30} label={false} />
              <span className="min-w-0 flex-1 truncate text-[17px] font-medium">{p.name}</span>
              <Meter value={clamp01(p.ownership)} tone={tab === "threats" ? "red" : "grass"} className="w-20 sm:w-32" delay={i * 0.04} />
              <span className="num w-11 text-right text-[17px] font-semibold">{pct(p.ownership)}</span>
            </RowButton>
          ))}
        </m.ul>
      ) : (
        <p className="py-4 text-[15px] text-fg-3">None</p>
      )}
    </Section>
  );
}

export function League({ d }: { d: Advice }) {
  const L = d.league;
  if (!L) return null;
  return (
    <div className="mx-auto w-full max-w-[640px] px-5 pb-8 md:px-0 desk:max-w-[1120px] desk:px-8">
      <Hero L={L} d={d} />
      <div className="desk:grid desk:grid-cols-[minmax(0,1fr)_400px] desk:gap-x-24">
        <Standings L={L} />
        <div>
          <Target L={L} d={d} />
          <Ownership L={L} d={d} />
        </div>
      </div>
    </div>
  );
}
