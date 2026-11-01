import { AnimatePresence, m, useReducedMotion } from "motion/react";
import { clock, dateTime } from "../lib/format";
import { useNow } from "../lib/useMedia";

/** The live deadline clock. Each digit rolls when it changes. */
export function Deadline({ iso, className = "" }: { iso?: string; className?: string }) {
  const t = Date.parse(iso || "");
  const now = useNow(1000);
  const reduce = useReducedMotion();
  if (!Number.isFinite(t)) return null;
  const ms = t - (now || Date.now());
  const urgent = ms > 0 && ms < 3 * 3600e3;
  const text = ms > 0 ? clock(ms) : "Passed";
  return (
    <span
      className={`num tabular-nums inline-flex items-baseline ${urgent ? "text-amber" : ""} ${className}`}
      title={`Deadline ${dateTime.format(t)}`}
    >
      <span className="sr-only">
        Deadline {dateTime.format(t)}, in {text}
      </span>
      <span aria-hidden="true" className="inline-flex">
        {text.split("").map((ch, i) =>
          /\d/.test(ch) && !reduce ? (
            <span key={i} className="relative inline-flex h-[1.2em] overflow-hidden">
              <AnimatePresence mode="popLayout" initial={false}>
                <m.span
                  key={ch}
                  initial={{ y: "-70%", opacity: 0 }}
                  animate={{ y: 0, opacity: 1 }}
                  exit={{ y: "70%", opacity: 0 }}
                  transition={{ type: "spring", stiffness: 420, damping: 32 }}
                  className="inline-block"
                >
                  {ch}
                </m.span>
              </AnimatePresence>
            </span>
          ) : (
            <span key={i} className={ch === ":" ? "px-[0.04em] text-fg-3" : ""}>
              {ch === " " ? "\u00a0" : ch}
            </span>
          ),
        )}
      </span>
    </span>
  );
}
