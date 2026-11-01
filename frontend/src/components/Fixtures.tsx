import { parseFixtures } from "../lib/format";

/** Upcoming fixtures as small chips: filled for home, outlined for away. */
export function Fixtures({ fixtures, max = 5, size = "md" }: { fixtures?: string; max?: number; size?: "xs" | "sm" | "md" }) {
  const list = parseFixtures(fixtures).slice(0, max);
  if (!list.length) return null;
  const pad = size === "xs" ? "h-6 px-1.5 text-[11px]" : size === "sm" ? "h-6 px-2 text-[11px]" : "h-7 px-2.5 text-xs";
  return (
    <ul className={`flex gap-1 ${size === "xs" ? "flex-nowrap" : "flex-wrap gap-1.5"}`} aria-label="Next fixtures">
      {list.map((f, i) => (
        <li
          key={i}
          className={`num inline-flex items-center rounded-full font-semibold tracking-wide ${pad} ${
            f.home ? "bg-raised text-fg" : "text-fg-2 ring-1 ring-line-2 ring-inset"
          }`}
        >
          {f.opp}
          <span className="sr-only">{f.home ? " at home" : f.home === false ? " away" : ""}</span>
          <span aria-hidden="true" className={`${size === "xs" ? "ml-0.5" : "ml-1"} font-medium text-fg-3`}>
            {f.home ? "H" : f.home === false ? "A" : ""}
          </span>
        </li>
      ))}
    </ul>
  );
}
