import { m, type Variants } from "motion/react";
import type { ReactNode } from "react";
import { availability, clamp01 } from "../lib/format";
import type { Player } from "../lib/types";

export const list: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.06, delayChildren: 0.08 } },
};
export const item: Variants = {
  hidden: { opacity: 0, y: 14 },
  show: { opacity: 1, y: 0, transition: { type: "spring", stiffness: 260, damping: 30 } },
};

/** A titled block. The title is quiet; an optional muted line under it says what the numbers mean. */
export function Section({
  title,
  sub,
  meta,
  children,
  className = "",
  id,
}: {
  title: string;
  sub?: ReactNode;
  meta?: ReactNode;
  children: ReactNode;
  className?: string;
  id?: string;
}) {
  return (
    <section aria-labelledby={id} className={className}>
      <header className="pb-2">
        <div className="flex items-baseline justify-between gap-4">
          <h2 id={id} className="text-[15px] font-medium text-fg-2">
            {title}
          </h2>
          {meta && <div className="text-right text-sm text-fg-3">{meta}</div>}
        </div>
        {sub && <p className="mt-0.5 text-[13px] text-fg-3">{sub}</p>}
      </header>
      {children}
    </section>
  );
}

/** A one-line key for the marks used in a list: shown, not hovered, so it works on phones. */
export function Legend({ items, className = "" }: { items: [ReactNode, string][]; className?: string }) {
  if (!items.length) return null;
  return (
    <ul className={`flex flex-wrap gap-x-5 gap-y-1.5 text-[13px] text-fg-3 ${className}`}>
      {items.map(([mark, label]) => (
        <li key={label} className="flex items-center gap-1.5">
          <span aria-hidden="true" className="inline-flex items-center">
            {mark}
          </span>
          {label}
        </li>
      ))}
    </ul>
  );
}

export const DotMark = ({ tone }: { tone: "bad" | "warn" | "grass" }) => (
  <span className={`inline-block size-2 rounded-full ${tone === "bad" ? "bg-red" : tone === "warn" ? "bg-amber-dot" : "bg-grass"}`} />
);

export function Chevron({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" className={`shrink-0 text-fg-3 ${className}`}>
      <path d="M9 6l6 6-6 6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** A full-width tappable row that opens detail. Rows are separated by hairlines. */
export function RowButton({
  children,
  onClick,
  label,
  className = "",
}: {
  children: ReactNode;
  onClick: () => void;
  label?: string;
  className?: string;
}) {
  return (
    <m.li variants={item} className="border-t border-line first:border-t-0">
      <m.button
        type="button"
        onClick={onClick}
        aria-label={label}
        whileTap={{ scale: 0.985 }}
        transition={{ type: "spring", stiffness: 600, damping: 35 }}
        className={`group -mx-3 flex w-[calc(100%+1.5rem)] items-center gap-4 rounded-2xl px-3 text-left transition-colors duration-200 hover:bg-raised/70 ${className}`}
      >
        {children}
      </m.button>
    </m.li>
  );
}

/** Small status dot: red for out, amber for doubtful. */
export function StatusDot({ p, className = "" }: { p?: Player | null; className?: string }) {
  const a = availability(p);
  if (!a) return null;
  return (
    <span
      title={a.text}
      className={`inline-block size-2 shrink-0 rounded-full ${a.kind === "bad" ? "bg-red" : "bg-amber-dot"} ${className}`}
    >
      <span className="sr-only">{a.text}</span>
    </span>
  );
}

export function Rise({ dir }: { dir?: "rise" | "fall" | null }) {
  if (dir !== "rise" && dir !== "fall") return null;
  return (
    <span className={`inline-flex items-center ${dir === "rise" ? "text-amber" : "text-red"}`} title={`Price likely to ${dir} tonight`}>
      <svg viewBox="0 0 10 10" width="9" height="9" aria-hidden="true">
        <path d={dir === "rise" ? "M5 1.5L9 8.5H1z" : "M5 8.5L1 1.5h8z"} fill="currentColor" />
      </svg>
      <span className="sr-only">Price likely to {dir} tonight</span>
    </span>
  );
}

/** A thin horizontal meter. */
export function Meter({
  value,
  tone = "grass",
  className = "",
  delay = 0,
}: {
  value: number;
  tone?: "grass" | "red" | "amber" | "fg";
  className?: string;
  delay?: number;
}) {
  const bg = { grass: "bg-grass", red: "bg-red", amber: "bg-amber", fg: "bg-fg-2" }[tone];
  return (
    <span aria-hidden="true" className={`block h-1.5 overflow-hidden rounded-full bg-raised ${className}`}>
      <m.span
        className={`block h-full origin-left rounded-full ${bg}`}
        initial={{ scaleX: 0 }}
        whileInView={{ scaleX: clamp01(value) }}
        viewport={{ once: true }}
        transition={{ type: "spring", stiffness: 120, damping: 24, delay }}
      />
    </span>
  );
}

/** Label / value pair for sheets. */
export function Stat({ k, v, tone }: { k: ReactNode; v: ReactNode; tone?: "grass" | "red" | "amber" }) {
  const c = tone === "grass" ? "text-grass-ink" : tone === "red" ? "text-red" : tone === "amber" ? "text-amber" : "";
  return (
    <div className="flex items-baseline justify-between gap-4 border-t border-line py-3 first:border-t-0">
      <dt className="text-[15px] text-fg-2">{k}</dt>
      <dd className={`num text-[17px] font-semibold ${c}`}>{v}</dd>
    </div>
  );
}

export function Prose({ children }: { children: ReactNode }) {
  return <div className="space-y-3 text-[16px] leading-relaxed text-fg-2">{children}</div>;
}

export function SubHead({ children }: { children: ReactNode }) {
  return <h3 className="mt-8 mb-2 text-[13px] font-medium text-fg-3">{children}</h3>;
}

/** A hover / focus tooltip for small marks on desktop. Screen readers get `label` from the
    element itself (aria-label or the surrounding control), so the bubble is aria-hidden. */
export function Tip({ label, children, className = "" }: { label: string; children: ReactNode; className?: string }) {
  return (
    <span className={`group/tip relative inline-flex ${className}`}>
      {children}
      <span
        aria-hidden="true"
        className="pointer-events-none absolute bottom-full left-1/2 z-20 mb-2 hidden -translate-x-1/2 translate-y-1 rounded-lg bg-fg px-2.5 py-1 text-[12px] font-medium whitespace-nowrap text-bg opacity-0 shadow-sm transition-[opacity,translate] duration-150 group-hover/tip:translate-y-0 group-hover/tip:opacity-100 md:block"
      >
        {label}
      </span>
    </span>
  );
}
