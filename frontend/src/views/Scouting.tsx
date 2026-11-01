import { m } from "motion/react";
import { useState, type ReactNode } from "react";
import { useSheet, type SheetSpec } from "../components/Sheet";
import { Segmented } from "../components/Tabs";
import { Token } from "../components/Token";
import { Chevron, list, Meter, RowButton, Section, SubHead, Tip } from "../components/ui";
import { CHIP_LABEL, int, isNum, ordinal, pct, safeUrl, shortDate } from "../lib/format";
import type { Advice, Consensus, Creator, Creators, Elite, Player, PlayerRef } from "../lib/types";
import { playerSheet } from "../sheets/sheets";

type EliteTab = "owned" | "missing" | "bought" | "sold";

function YourDot({ on }: { on?: boolean }) {
  if (!on) return null;
  return (
    <Tip label="In your team" className="shrink-0">
      <span role="img" aria-label="In your team" className="inline-block size-1.5 rounded-full bg-grass" />
    </Tip>
  );
}

function EliteBlock({ E, d }: { E: Elite; d: Advice }) {
  const show = useSheet();
  const [tab, setTab] = useState<EliteTab>("owned");
  const lists: Record<EliteTab, Player[]> = {
    owned: E.most_owned || [],
    missing: E.missing || [],
    bought: E.bought || [],
    sold: E.sold || [],
  };
  const rows = lists[tab];
  const maxN = Math.max(1, ...rows.map((p) => (tab === "bought" ? p.elite_in : p.elite_out) || 0));
  const seasons = E.seasons?.length ? `${E.seasons[0]}–${E.seasons[E.seasons.length - 1]}` : null;
  return (
    <Section
      title={`Top ${E.managers ?? ""} managers`}
      meta={isNum(E.gameweek) ? `GW${E.gameweek}` : null}
      className="pt-10 md:pt-20 desk:pt-24"
    >
      <Segmented
        label="Elite list"
        value={tab}
        onChange={setTab}
        className="mt-2 mb-4"
        items={[
          { id: "owned", label: "Owned" },
          { id: "missing", label: "Missing" },
          { id: "bought", label: "Bought" },
          { id: "sold", label: "Sold" },
        ]}
      />
      {rows.length ? (
        <m.ul key={tab} variants={list} initial="hidden" animate="show">
          {rows.map((p, i) => {
            const frac =
              tab === "owned" || tab === "missing"
                ? p.elite_own || 0
                : ((tab === "bought" ? p.elite_in : p.elite_out) || 0) / maxN;
            const val =
              tab === "owned" || tab === "missing"
                ? pct(p.elite_own)
                : tab === "bought"
                  ? `+${p.elite_in ?? 0}`
                  : `−${p.elite_out ?? 0}`;
            return (
              <RowButton key={p.id} className="py-3.5" label={`${p.name}${p.in_your_team ? ", in your team" : ""}`} onClick={() => show(playerSheet(p, d))}>
                <Token team={p.team} size={30} label={false} />
                <span className="flex min-w-0 flex-1 items-center gap-2">
                  <span className="truncate text-[17px] font-medium">{p.name}</span>
                  <YourDot on={p.in_your_team} />
                </span>
                {tab === "owned" && isNum(p.elite_captain) && p.elite_captain >= 0.05 && (
                  <span className="num hidden text-[13px] text-fg-3 sm:inline">{pct(p.elite_captain)} C</span>
                )}
                <Meter value={frac} tone={tab === "sold" ? "red" : "grass"} className="w-16 sm:w-28" delay={i * 0.04} />
                <span className={`num w-11 text-right text-[17px] font-semibold ${tab === "sold" ? "text-red" : ""}`}>{val}</span>
              </RowButton>
            );
          })}
        </m.ul>
      ) : (
        <p className="py-4 text-[15px] text-fg-3">None</p>
      )}
      {(seasons || isNum(E.median_rank)) && (
        <p className="mt-6 text-[13px] text-fg-3">
          Best typical finish{seasons ? `, ${seasons}` : ""}
          {isNum(E.median_rank) && (
            <>
              {" "}
              · around <span className="num">{int(Math.round(E.median_rank))}</span>
              {ordinal(Math.round(E.median_rank))}
            </>
          )}
        </p>
      )}
    </Section>
  );
}

const KIND: Record<string, { label: string; cls: string; seg: string }> = {
  buy: { label: "Buy", cls: "text-grass-ink", seg: "bg-grass" },
  sell: { label: "Sell", cls: "text-red", seg: "bg-red" },
  captain: { label: "Captain", cls: "text-fg", seg: "bg-fg" },
};

function Segs({ count, of, cls }: { count?: number; of?: number; cls: string }) {
  if (!isNum(count) || !isNum(of) || of <= 0 || of > 20) return null;
  return (
    <span className="flex gap-[3px]" aria-hidden="true">
      {Array.from({ length: of }, (_, i) => (
        <m.span
          key={i}
          initial={{ scaleY: 0.3, opacity: 0 }}
          whileInView={{ scaleY: 1, opacity: 1 }}
          viewport={{ once: true }}
          transition={{ type: "spring", stiffness: 300, damping: 20, delay: i * 0.05 }}
          className={`h-3.5 w-1.5 rounded-full ${i < count ? cls : "bg-raised"}`}
        />
      ))}
    </span>
  );
}

function PlayerLabel({ p, fallback }: { p?: PlayerRef; fallback: string }) {
  if (!p?.name) return <span className="min-w-0 flex-1 text-[15px] text-fg-2">{fallback}</span>;
  return (
    <>
      <Token team={p.team} size={30} label={false} />
      <span className="flex min-w-0 flex-1 items-center gap-2">
        <span className="truncate text-[17px] font-medium">{p.name}</span>
        <YourDot on={p.in_your_team} />
      </span>
    </>
  );
}

function AgreeRow({ a }: { a: Consensus }) {
  const k = KIND[a.kind] || KIND.captain;
  return (
    <m.li variants={{ hidden: { opacity: 0, y: 10 }, show: { opacity: 1, y: 0 } }} className="flex items-center gap-4 border-t border-line py-3.5 first:border-t-0">
      <span className="sr-only">{a.text}</span>
      <span aria-hidden="true" className="contents">
        <PlayerLabel p={a.player} fallback={a.text} />
        <span className={`text-[15px] font-medium ${k.cls}`}>{k.label}</span>
        <Segs count={a.count} of={a.of} cls={k.seg} />
        <span className="num w-8 text-right text-[15px] text-fg-2">
          {a.count}/{a.of}
        </span>
      </span>
    </m.li>
  );
}

function SplitRow({ x }: { x: Consensus }) {
  let bar: ReactNode = null;
  if (x.kind === "captain" && Array.isArray(x.options)) {
    const opts = x.options.filter((o) => o.player?.name);
    bar = (
      <span className="flex min-w-0 flex-1 flex-wrap items-center justify-end gap-x-5 gap-y-2">
        {opts.map((o, i) => (
          <span key={i} className="flex min-w-0 items-center gap-2">
            <Token team={o.player!.team} size={24} label={false} />
            <span className="truncate text-[15px] font-medium">{o.player!.name}</span>
            <span className="num text-[15px] text-fg-2">{o.who?.length ?? 0}</span>
          </span>
        ))}
      </span>
    );
    return (
      <m.li variants={{ hidden: { opacity: 0, y: 10 }, show: { opacity: 1, y: 0 } }} className="flex items-center gap-4 border-t border-line py-3.5 first:border-t-0">
        <span className="sr-only">{x.text}</span>
        <span aria-hidden="true" className="contents">
          <span className="text-[15px] font-medium text-fg-2">Captain</span>
          {bar}
        </span>
      </m.li>
    );
  }
  const b = x.buying?.length ?? 0;
  const s = x.selling?.length ?? 0;
  return (
    <m.li variants={{ hidden: { opacity: 0, y: 10 }, show: { opacity: 1, y: 0 } }} className="flex items-center gap-4 border-t border-line py-3.5 first:border-t-0">
      <span className="sr-only">{x.text}</span>
      <span aria-hidden="true" className="contents">
        <PlayerLabel p={x.player} fallback={x.text} />
        {b + s > 0 && (
          <span className="num flex items-center gap-3 text-[15px] font-medium">
            <span className="text-grass-ink">Buy {b}</span>
            <span className="text-red">Sell {s}</span>
          </span>
        )}
      </span>
    </m.li>
  );
}

function initials(name: string) {
  const words = String(name || "")
    .replace(/['’]/g, "")
    .replace(/[^\p{L}\p{N}\s]/gu, " ")
    .split(/\s+/)
    .filter(Boolean);
  const pick = words.filter((w) => w.toUpperCase() !== "FPL");
  const src = pick.length ? pick : words;
  return (src.length > 1 ? src[0][0] + src[1][0] : (src[0] || "?").slice(0, 2)).toUpperCase();
}

const isRef = (p: unknown): p is PlayerRef => !!p && typeof p === "object" && typeof (p as PlayerRef).name === "string";
const refs = (xs: unknown) => (Array.isArray(xs) ? xs.filter(isRef) : []);

function Pill({ p, tone, maybe }: { p: PlayerRef; tone: "in" | "out" | "cap"; maybe?: boolean }) {
  const solid = tone === "in" ? "ring-grass/60" : tone === "out" ? "ring-red/60" : "ring-fg-3";
  const dashed = tone === "in" ? "border-grass/60" : tone === "out" ? "border-red/60" : "border-fg-3";
  return (
    <span
      className={`inline-flex h-9 items-center gap-2 rounded-full pr-3.5 pl-1.5 text-[15px] ${
        maybe ? `border border-dashed text-fg-2 ${dashed}` : `ring-1 ring-inset ${solid}`
      }`}
      title={maybe ? `${p.name}: still deciding` : p.name}
    >
      <Token team={p.team} size={24} label={false} />
      {p.name}
      {maybe && <span className="sr-only">(thinking about it)</span>}
    </span>
  );
}

function Group({ label, children }: { label: string; children: ReactNode[] }) {
  if (!children.length) return null;
  return (
    <div className="mt-6">
      <h4 className="mb-2.5 text-[13px] font-medium text-fg-3">{label}</h4>
      <div className="flex flex-wrap gap-2">{children}</div>
    </div>
  );
}

function creatorSheet(c: Creator, R: Creators): SheetSpec {
  const v = c.video && typeof c.video === "object" ? c.video : null;
  const vurl = v ? safeUrl(v.url) : null;
  const curl = safeUrl(c.channel_url);
  const dIn = refs(c.decided_in);
  const dOut = refs(c.decided_out);
  const inIds = new Set(dIn.map((p) => p.id));
  const outIds = new Set(dOut.map((p) => p.id));
  const buying = [
    ...dIn.map((p) => <Pill key={`d${p.id}`} p={p} tone="in" />),
    ...refs(c.considering_in)
      .filter((p) => !inIds.has(p.id))
      .map((p) => <Pill key={`c${p.id}`} p={p} tone="in" maybe />),
  ];
  const selling = [
    ...dOut.map((p) => <Pill key={`d${p.id}`} p={p} tone="out" />),
    ...refs(c.considering_out)
      .filter((p) => !outIds.has(p.id))
      .map((p) => <Pill key={`c${p.id}`} p={p} tone="out" maybe />),
  ];
  const caps = isRef(c.captain)
    ? [<Pill key="c" p={c.captain} tone="cap" />]
    : refs(c.captain_options).map((p) => <Pill key={p.id} p={p} tone="cap" maybe />);
  const A = c.actual && typeof c.actual === "object" ? c.actual : null;
  return {
    title: c.name,
    head: (
      <span className="flex items-center gap-4">
        <span className="grid size-12 place-items-center rounded-full bg-raised text-[15px] font-semibold text-fg-2">{initials(c.name)}</span>
        <span>
          {curl ? (
            <a href={curl} target="_blank" rel="noopener noreferrer" className="underline decoration-line-2 underline-offset-4 hover:decoration-fg">
              {c.name}
            </a>
          ) : (
            c.name
          )}
          {v?.published && <span className="block text-[15px] font-normal text-fg-2">{shortDate(v.published)}</span>}
        </span>
      </span>
    ),
    body: (
      <>
        {v ? (
          vurl ? (
            <a href={vurl} target="_blank" rel="noopener noreferrer" className="group block rounded-2xl bg-raised px-4 py-4 transition-colors hover:bg-line-2">
              <span className="block text-[16px] leading-snug font-medium">{v.title}</span>
              <span className="mt-1 block text-[13px] text-fg-3">Watch on YouTube ↗</span>
            </a>
          ) : (
            <p className="rounded-2xl bg-raised px-4 py-4 text-[16px] font-medium">{v.title}</p>
          )
        ) : (
          <p className="text-[15px] text-fg-3">No GW{R.gameweek} video yet</p>
        )}
        {c.summary ? (
          <p className="mt-6 text-[16px] leading-relaxed text-fg-2">{c.summary}</p>
        ) : (
          v && R.read_by_llm && !c.read && <p className="mt-6 text-[15px] text-fg-3">No plan found in the description.</p>
        )}
        {CHIP_LABEL[c.chip || ""] && (
          <p className="mt-6 inline-flex h-8 items-center rounded-full bg-grass-soft px-3.5 text-[15px] font-medium text-grass-ink">
            {CHIP_LABEL[c.chip || ""]}
          </p>
        )}
        <Group label="Buying">{buying}</Group>
        <Group label="Selling">{selling}</Group>
        <Group label="Captain">{caps}</Group>
        {A && (
          <>
            <SubHead>Did in GW{A.gameweek}</SubHead>
            <div className="flex flex-wrap gap-2">
              {refs(A.transfers_in).map((p) => (
                <Pill key={`i${p.id}`} p={p} tone="in" />
              ))}
              {refs(A.transfers_out).map((p) => (
                <Pill key={`o${p.id}`} p={p} tone="out" />
              ))}
              {isRef(A.captain) && <Pill p={A.captain} tone="cap" />}
              {!refs(A.transfers_in).length && !refs(A.transfers_out).length && (
                <span className="text-[15px] text-fg-3">No transfers</span>
              )}
              {CHIP_LABEL[A.chip || ""] && (
                <span className="inline-flex h-9 items-center rounded-full bg-grass-soft px-3.5 text-[15px] text-grass-ink">{CHIP_LABEL[A.chip || ""]}</span>
              )}
              {isNum(A.hit) && A.hit > 0 && (
                <span className="num inline-flex h-9 items-center rounded-full bg-red-soft px-3.5 text-[15px] text-red">−{A.hit}</span>
              )}
            </div>
          </>
        )}
        <p className="mt-10 text-[13px] leading-relaxed text-fg-3">
          {R.read_by_llm ? "Summarised by AI from the video's title and description; it can be wrong. " : ""}
          Creators watch each other, so agreement is closer to one opinion than many. This doesn't change your advice.
        </p>
      </>
    ),
  };
}

function CreatorsBlock({ R }: { R: Creators }) {
  const show = useSheet();
  const agree = R.agree || [];
  const split = R.split || [];
  const n = R.creators.length;
  const enough = R.read_by_llm && isNum(R.with_plan) && R.with_plan >= 2;
  return (
    <>
      {enough && (agree.length > 0 || split.length > 0) && (
        <Section title="Creators agree" meta={isNum(R.gameweek) ? `GW${R.gameweek}` : null} className="mt-16 md:mt-24 desk:mt-0">
          {agree.length ? (
            <m.ul variants={list} initial="hidden" whileInView="show" viewport={{ once: true }}>
              {agree.map((a, i) => (
                <AgreeRow key={i} a={a} />
              ))}
            </m.ul>
          ) : (
            <p className="py-4 text-[15px] text-fg-3">No consensus</p>
          )}
          {split.length > 0 && (
            <>
              <h3 className="mt-10 pb-2 text-[15px] font-medium text-fg-2">Split</h3>
              <m.ul variants={list} initial="hidden" whileInView="show" viewport={{ once: true }}>
                {split.map((x, i) => (
                  <SplitRow key={i} x={x} />
                ))}
              </m.ul>
            </>
          )}
        </Section>
      )}
      <Section
        title="Creators"
        meta={isNum(R.with_video) ? `${R.with_video} of ${n} posted` : null}
        className="mt-16 md:mt-20"
      >
        <m.ul variants={list} initial="hidden" whileInView="show" viewport={{ once: true }}>
          {R.creators.map((c) => {
            const v = c.video && typeof c.video === "object" ? c.video : null;
            return (
              <RowButton key={c.name} className="py-3.5" onClick={() => show(creatorSheet(c, R))} label={c.name}>
                <span aria-hidden="true" className="grid size-10 shrink-0 place-items-center rounded-full bg-raised text-[13px] font-semibold text-fg-2">
                  {initials(c.name)}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[17px] font-medium">{c.name}</span>
                  <span className="block truncate text-[13px] text-fg-3">{v ? v.title : `No GW${R.gameweek} video yet`}</span>
                </span>
                {CHIP_LABEL[c.chip || ""] && (
                  <span className="hidden h-7 items-center rounded-full bg-grass-soft px-3 text-[13px] font-medium text-grass-ink sm:inline-flex">
                    {CHIP_LABEL[c.chip || ""]}
                  </span>
                )}
                <Chevron />
              </RowButton>
            );
          })}
        </m.ul>
      </Section>
    </>
  );
}

export function Scouting({ d }: { d: Advice }) {
  const E = d.elite && d.elite.most_owned?.length ? d.elite : null;
  const R = d.creators && Array.isArray(d.creators.creators) && d.creators.creators.length ? d.creators : null;
  return (
    <div className="mx-auto w-full max-w-[640px] px-5 pb-8 md:px-0 desk:grid desk:max-w-[1120px] desk:grid-cols-2 desk:gap-x-24 desk:px-8">
      <div>{E ? <EliteBlock E={E} d={d} /> : <div className="pt-6" />}</div>
      {R && (
        <div className="desk:pt-24">
          <CreatorsBlock R={R} />
        </div>
      )}
    </div>
  );
}

export const hasScouting = (d: Advice) =>
  !!(d.elite && d.elite.most_owned?.length) || !!(d.creators && d.creators.creators?.length);
