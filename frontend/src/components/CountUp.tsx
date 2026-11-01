import { useInView, useReducedMotion } from "motion/react";
import { useEffect, useRef } from "react";

interface Props {
  value: number;
  decimals?: number;
  sign?: boolean;
  className?: string;
  delay?: number;
}

const fmt = (v: number, d: number, sign: boolean) => {
  const s = Math.abs(v).toFixed(d);
  const grouped = d === 0 ? Number(s).toLocaleString() : s;
  return sign ? `${v < 0 ? "−" : "+"}${grouped}` : v < 0 ? `−${grouped}` : grouped;
};

/** A number that counts up once, the first time it scrolls into view (an expo ease-out on rAF). */
export function CountUp({ value, decimals = 1, sign = false, className, delay = 0 }: Props) {
  const ref = useRef<HTMLSpanElement>(null);
  const reduce = useReducedMotion();
  const seen = useInView(ref, { once: true, margin: "0px 0px -10% 0px" });
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (reduce) {
      el.textContent = fmt(value, decimals, sign);
      return;
    }
    if (!seen) return;
    let raf = 0;
    const t0 = performance.now() + delay * 1000;
    const dur = 1100;
    const step = (t: number) => {
      const k = Math.max(0, Math.min(1, (t - t0) / dur));
      const e = k === 1 ? 1 : 1 - Math.pow(2, -10 * k);
      el.textContent = fmt(value * e, decimals, sign);
      if (k < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [seen, value, decimals, sign, reduce, delay]);
  return (
    <span ref={ref} className={className}>
      {fmt(reduce ? value : 0, decimals, sign)}
    </span>
  );
}
