import { AnimatePresence, LayoutGroup, m } from "motion/react";
import { type ReactNode, useState } from "react";
import { useSheet } from "../components/Sheet";
import { Segmented } from "../components/Tabs";
import { Token } from "../components/Token";
import { DotMark, Legend } from "../components/ui";
import { availability, f1, int, isNum, money } from "../lib/format";
import { useMedia } from "../lib/useMedia";
import type { Advice, Player, Sheet } from "../lib/types";
import { playerSheet } from "../sheets/sheets";

const ROWS = ["GKP", "DEF", "MID", "FWD"] as const;
const move = { type: "spring", stiffness: 260, damping: 30, mass: 0.9 } as const;

function Markings({ h }: { h: number }) {
  const s = "var(--pitch-line)";
  const half = h - 5;
  return (
    <svg
      viewBox={`0 0 100 ${h}`}
      className="absolute inset-0 size-full"
      aria-hidden="true"
      fill="none"
      stroke={s}
      strokeWidth="0.35"
    >
      <rect x="4" y="4" width="92" height={h - 4} rx="0.6" />
      <rect x="22" y="4" width="56" height="17" />
      <rect x="37" y="4" width="26" height="6" />
      <path d="M41 21 A 9.5 9.5 0 0 0 59 21" />
      <circle cx="50" cy="15.5" r="0.5" fill={s} stroke="none" />
      <line x1="4" y1={half} x2="96" y2={half} />
      <path d={`M37 ${half} A 13 13 0 0 1 63 ${half}`} />
      <circle cx="50" cy={half} r="0.6" fill={s} stroke="none" />
    </svg>
  );
}

function PitchPlayer({
  p,
  cv,
  fresh,
  onOpen,
  size,
  onPitch = true,
  order,
}: {
  p: Player;
  cv: "C" | "V" | null;
  fresh: boolean;
  onOpen: () => void;
  size: number;
  onPitch?: boolean;
  order?: number;
}) {
  const a = availability(p);
  const label = [
    order ? `Bench ${order}` : null,
    p.name,
    p.team,
    p.next_fixture ? `v ${p.next_fixture}` : null,
    `${f1(p.xp_next)} expected points`,
    cv === "C" ? "captain" : cv === "V" ? "vice-captain" : null,
    fresh ? "new signing" : null,
    a ? a.text : null,
  ]
    .filter(Boolean)
    .join(", ");
  return (
    <m.li
      layout
      layoutId={`pl-${p.id}`}
      initial={{ opacity: 0, y: 18, scale: 0.85 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, scale: 0.7, transition: { duration: 0.18 } }}
      transition={move}
      className="flex min-w-0 flex-1 justify-center"
    >
      <m.button
        type="button"
        onClick={onOpen}
        aria-label={label}
        whileHover={{ y: -2 }}
        whileTap={{ scale: 0.94 }}
        transition={{ type: "spring", stiffness: 500, damping: 30 }}
        className="flex w-full max-w-[88px] flex-col items-center rounded-xl outline-offset-4 desk:max-w-none"
      >
        <span className="relative">
          <span
            className={`block rounded-full ${fresh ? "ring-2 ring-white/85 ring-offset-2 ring-offset-transparent" : ""}`}
          >
            <Token team={p.team} size={size} />
          </span>
          {cv && (
            <m.span
              layoutId={cv === "C" ? "badge-c" : "badge-v"}
              transition={move}
              className={`absolute -top-1 -right-1.5 grid size-[19px] place-items-center rounded-full text-[10px] font-bold shadow-sm ${
                cv === "C" ? "bg-white text-black" : "bg-black/70 text-white"
              }`}
            >
              {cv}
            </m.span>
          )}
          {a && (
            <span
              className={`absolute -top-0.5 -left-0.5 size-2.5 rounded-full ring-2 ${onPitch ? "ring-[var(--pitch-a)]" : "ring-[var(--bench)]"} ${
                a.kind === "bad" ? "bg-red" : "bg-amber-dot"
              }`}
            />
          )}
        </span>
        <span
          className={`mt-2 max-w-full truncate rounded-md px-1.5 py-[3px] leading-tight font-semibold sm:px-2 desk:max-w-none desk:overflow-visible desk:px-2.5 desk:text-[14px] ${
            p.name.length > 9 ? "text-[11px] sm:text-[12.5px]" : "text-[12px] sm:text-[13px]"
          } ${
            onPitch ? "bg-[var(--plate)] text-white" : "text-fg"
          }`}
        >
          {p.name}
        </span>
        <span className={`num mt-1 text-[12px] font-medium sm:text-[13px] ${onPitch ? "text-white/75" : "text-fg-2"}`}>
          {f1(p.xp_next)}
        </span>
      </m.button>
    </m.li>
  );
}

function Pitch({ s, fresh, d }: { s: Sheet; fresh: Set<number>; d: Advice }) {
  const show = useSheet();
  const wide = useMedia("(min-width: 768px)");
  const desk = useMedia("(min-width: 1100px)");
  const h = desk ? 80 : wide ? 100 : 132;
  const size = desk ? 56 : wide ? 50 : 42;
  const tops = desk ? [6, 30, 54, 77] : wide ? [7, 30, 53, 76] : [6, 29, 53, 77];
  const cvOf = (p: Player) => (p.id === s.captain_id ? "C" : p.id === s.vice_id ? "V" : null);
  const bench = s.bench || [];
  return (
    <>
      <div
        className="pitch relative overflow-hidden rounded-[28px] shadow-[inset_0_0_0_1px_var(--pitch-edge)]"
        style={{ aspectRatio: `100 / ${h}` }}
      >
        <Markings h={h} />
        {ROWS.map((pos, r) => {
          const row = s.xi.filter((p) => p.pos === pos);
          return (
            <ul
              key={pos}
              aria-label={{ GKP: "Goalkeeper", DEF: "Defenders", MID: "Midfielders", FWD: "Forwards" }[pos]}
              className="absolute inset-x-2 flex justify-center sm:inset-x-6"
              style={{ top: `${tops[r]}%` }}
            >
              <AnimatePresence mode="popLayout" initial={false}>
                {row.map((p) => (
                  <PitchPlayer
                    key={p.id}
                    p={p}
                    cv={cvOf(p)}
                    fresh={fresh.has(p.id)}
                    size={size}
                    onOpen={() => show(playerSheet(p, d, { cv: cvOf(p) ?? undefined }))}
                  />
                ))}
              </AnimatePresence>
            </ul>
          );
        })}
      </div>
      {bench.length > 0 && (
        <div className="mt-4 rounded-[28px] bg-[var(--bench)] px-2 pt-5 pb-4 sm:px-6">
          <h3 className={`mb-3 text-center text-[13px] font-medium ${s.bench_boost ? "text-grass-ink" : "text-fg-3"}`}>
            {s.bench_boost ? "Bench Boost: the bench scores too" : "Bench: substitutes, in order"}
          </h3>
          <ul aria-label="Bench, in order" className="flex justify-center">
            <AnimatePresence mode="popLayout" initial={false}>
              {bench.map((p, i) => (
                <PitchPlayer
                  key={p.id}
                  p={p}
                  cv={cvOf(p)}
                  fresh={fresh.has(p.id)}
                  size={size - 4}
                  onPitch={false}
                  order={i + 1}
                  onOpen={() => show(playerSheet(p, d))}
                />
              ))}
            </AnimatePresence>
          </ul>
        </div>
      )}
    </>
  );
}

function Badge({ k }: { k: "C" | "V" }) {
  return (
    <span
      className={`grid size-[17px] place-items-center rounded-full text-[10px] font-bold ${
        k === "C" ? "bg-fg text-bg" : "bg-fg-3 text-bg"
      }`}
    >
      {k}
    </span>
  );
}

export function Team({ d }: { d: Advice }) {
  const L = d.lineup;
  const C = L?.current && L.current.xi?.length ? L.current : null;
  const [view, setView] = useState<"now" | "after">("now");
  const show = useSheet();
  if (!L || !L.xi?.length) return null;
  const sheet = C && view === "now" ? C : L;
  const ids = (s: Sheet) => new Set([...s.xi, ...(s.bench || [])].map((p) => p.id));
  const fresh = C && view === "after" ? new Set([...ids(L)].filter((id) => !ids(C).has(id))) : new Set<number>();
  const t = d.team || {};
  const facts = [
    isNum(t.value) ? ["Squad value", money(t.value)] : null,
    isNum(t.bank) ? ["In the bank", money(t.bank)] : null,
    isNum(t.total_points) ? ["Total points", int(t.total_points)] : null,
  ].filter(Boolean) as [string, string][];
  const caption = (cls: string) =>
    C ? <p className={`text-[13px] text-fg-3 ${cls}`}>Numbers are expected points this week</p> : null;
  const legendItems: [ReactNode, string][] = [
    [<Badge key="c" k="C" />, "Captain"],
    [<Badge key="v" k="V" />, "Vice-captain"],
    [<DotMark key="b" tone="bad" />, "Injured or out"],
    [<DotMark key="w" tone="warn" />, "Doubtful"],
    ...(fresh.size ? ([[<span key="n" className="inline-block size-3 rounded-full ring-2 ring-fg-2" />, "New signing"]] as [ReactNode, string][]) : []),
    [<span key="x" className="num text-fg-2">5.2</span>, "Expected points this week"],
  ];
  const toggle = (cls: string, stack = false) =>
    C ? (
      <Segmented
        label="Which team to show"
        value={view}
        onChange={setView}
        size="lg"
        className={cls}
        items={[
          {
            id: "now",
            name: `Now, ${f1(C.expected_points)} expected points`,
            label: (
              <>
                <span className={stack ? "flex flex-col leading-tight" : "contents"}>
                  <span className="whitespace-nowrap">Your team now</span>
                  <span className="num text-fg-2">{f1(C.expected_points)}</span>
                </span>
              </>
            ),
          },
          {
            id: "after",
            name: `After the suggested moves, ${f1(L.expected_points)} expected points`,
            label: (
              <>
                <span className={stack ? "flex flex-col leading-tight" : "contents"}>
                  <span className="whitespace-nowrap">After our moves</span>
                  <span className="num text-grass-ink">{f1(L.expected_points)}</span>
                </span>
              </>
            ),
          },
        ]}
      />
    ) : (
      <p className={`text-[15px] text-fg-2 ${cls}`}>
        <span className="num text-[17px] font-semibold text-fg">{f1(L.expected_points)}</span> expected points this week
      </p>
    );
  const flagged = [...sheet.xi, ...(sheet.bench || [])].filter((p) => availability(p));
  return (
    <div className="mx-auto w-full max-w-[600px] px-4 pt-8 pb-8 md:px-0 md:pt-14 desk:grid desk:max-w-[1120px] desk:grid-cols-[minmax(0,1fr)_260px] desk:gap-x-14 desk:px-8">
      <div>
        {toggle("mx-auto max-w-[420px] text-center desk:hidden")}
        {caption("mt-2 mb-6 text-center desk:hidden")}
        {!C && <div className="mb-6 desk:hidden" />}
        <LayoutGroup>
          <Pitch s={sheet} fresh={fresh} d={d} />
        </LayoutGroup>
        <Legend items={legendItems} className="mt-4 justify-center" />
      </div>
      <aside className="desk:sticky desk:top-28 desk:self-start">
        {toggle("hidden desk:grid", true)}
        {caption("hidden desk:block desk:mt-2")}
        <div className="hidden desk:block desk:mb-12" />
        {facts.length > 0 && (
          <dl className="mt-10 grid grid-cols-3 gap-4 text-center desk:mt-0 desk:grid-cols-1 desk:gap-0 desk:text-left">
            {facts.map(([k, v]) => (
              <div key={k} className="flex flex-col-reverse desk:flex-row desk:items-baseline desk:justify-between desk:border-t desk:border-line desk:py-4 desk:first:border-t-0">
                <dt className="mt-1 text-[13px] text-fg-3 desk:mt-0 desk:text-[15px] desk:text-fg-2">{k}</dt>
                <dd className="num text-[20px] font-semibold">{v}</dd>
              </div>
            ))}
          </dl>
        )}
        {flagged.length > 0 && (
          <ul className="hidden space-y-3 desk:mt-12 desk:block">
            {flagged.map((p) => {
              const a = availability(p)!;
              return (
                <li key={p.id}>
                  <button
                    type="button"
                    onClick={() => show(playerSheet(p, d))}
                    className="flex w-full items-start gap-3 rounded-xl text-left text-[14px] leading-snug transition-colors hover:text-fg"
                  >
                    <span className={`mt-1.5 size-2 shrink-0 rounded-full ${a.kind === "bad" ? "bg-red" : "bg-amber-dot"}`} aria-hidden="true" />
                    <span>
                      <span className="font-medium text-fg">{p.name}</span> <span className="text-fg-2">{a.text}</span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </aside>
    </div>
  );
}
