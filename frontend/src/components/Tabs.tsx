import { m } from "motion/react";
import { useId, useRef, type KeyboardEvent, type ReactNode } from "react";

export interface Item<T extends string> {
  id: T;
  label: ReactNode;
  /** Accessible name when the label is not plain text. */
  name?: string;
}

const pillSpring = { type: "spring", stiffness: 520, damping: 40, mass: 0.8 } as const;

function useRoving<T extends string>(items: Item<T>[], value: T, onChange: (v: T) => void) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const onKeyDown = (e: KeyboardEvent) => {
    const i = items.findIndex((t) => t.id === value);
    let n = -1;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") n = (i + 1) % items.length;
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") n = (i - 1 + items.length) % items.length;
    else if (e.key === "Home") n = 0;
    else if (e.key === "End") n = items.length - 1;
    if (n < 0) return;
    e.preventDefault();
    onChange(items[n].id);
    refs.current[n]?.focus();
  };
  return { refs, onKeyDown };
}

/** The main view tabs: a sliding pill under the active label. Arrow keys, Home and End move. */
export function TabBar<T extends string>({
  items,
  value,
  onChange,
  className = "",
  pill = "view-pill",
}: {
  items: Item<T>[];
  value: T;
  onChange: (v: T) => void;
  className?: string;
  pill?: string;
}) {
  const { refs, onKeyDown } = useRoving(items, value, onChange);
  return (
    <div role="tablist" aria-label="Views" className={`flex items-center gap-1 ${className}`} onKeyDown={onKeyDown}>
      {items.map((t, i) => {
        const on = t.id === value;
        return (
          <button
            key={t.id}
            ref={(el) => {
              refs.current[i] = el;
            }}
            role="tab"
            id={`tab-${t.id}`}
            aria-controls={`panel-${t.id}`}
            aria-selected={on}
            tabIndex={on ? 0 : -1}
            onClick={() => onChange(t.id)}
            className={`relative h-9 rounded-full px-3.5 text-[15px] font-medium whitespace-nowrap transition-colors duration-200 sm:px-4 ${
              on ? "text-fg" : "text-fg-2 hover:text-fg"
            }`}
          >
            {on && (
              <m.span
                layoutId={pill}
                transition={pillSpring}
                className="absolute inset-0 rounded-full bg-raised"
                aria-hidden="true"
              />
            )}
            <span className="relative">{t.label}</span>
          </button>
        );
      })}
    </div>
  );
}

/** A two-to-four way toggle (radio group) with a sliding thumb. */
export function Segmented<T extends string>({
  items,
  value,
  onChange,
  label,
  className = "",
  size = "md",
}: {
  items: Item<T>[];
  value: T;
  onChange: (v: T) => void;
  label: string;
  className?: string;
  size?: "md" | "lg";
}) {
  const uid = useId();
  const { refs, onKeyDown } = useRoving(items, value, onChange);
  const h = size === "lg" ? "h-14" : "h-10";
  return (
    <div
      role="radiogroup"
      aria-label={label}
      onKeyDown={onKeyDown}
      className={`grid rounded-full bg-raised p-1 ${className}`}
      style={{ gridTemplateColumns: `repeat(${items.length}, minmax(0, 1fr))` }}
    >
      {items.map((t, i) => {
        const on = t.id === value;
        return (
          <button
            key={t.id}
            ref={(el) => {
              refs.current[i] = el;
            }}
            role="radio"
            aria-checked={on}
            aria-label={t.name}
            tabIndex={on ? 0 : -1}
            onClick={() => onChange(t.id)}
            className={`relative ${h} rounded-full px-1.5 text-[15px] font-medium whitespace-nowrap transition-colors sm:px-3 duration-200 ${
              on ? "text-fg" : "text-fg-2 hover:text-fg"
            }`}
          >
            {on && (
              <m.span
                layoutId={`seg-${uid}`}
                transition={pillSpring}
                className="absolute inset-0 rounded-full bg-[var(--c-thumb)] shadow-[0_1px_3px_rgb(0_0_0/0.16)]"
                aria-hidden="true"
              />
            )}
            <span className="relative flex items-center justify-center gap-2">{t.label}</span>
          </button>
        );
      })}
    </div>
  );
}
