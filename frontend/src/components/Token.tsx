import { useId } from "react";
import { isLight, kitOf } from "../lib/kits";

interface Props {
  team?: string;
  size?: number;
  label?: boolean;
  dim?: boolean;
  className?: string;
}

/** A club disc: the club's colours with its stripe or trim, a soft top light and a crisp edge. */
export function Token({ team, size = 40, label = size >= 34, dim = false, className = "" }: Props) {
  const [a, b, pat] = kitOf(team);
  const uid = useId().replace(/:/g, "");
  const code = String(team || "").toUpperCase().slice(0, 3);
  const ink = isLight(a) ? "#0d1510" : "#ffffff";
  return (
    <svg
      viewBox="0 0 48 48"
      width={size}
      height={size}
      aria-hidden="true"
      focusable="false"
      className={`shrink-0 ${dim ? "opacity-50" : ""} ${className}`}
    >
      <defs>
        <clipPath id={`c${uid}`}>
          <circle cx="24" cy="24" r="23" />
        </clipPath>
        <radialGradient id={`l${uid}`} cx="34%" cy="22%" r="80%">
          <stop offset="0" stopColor="#fff" stopOpacity="0.32" />
          <stop offset="0.45" stopColor="#fff" stopOpacity="0.04" />
          <stop offset="1" stopColor="#000" stopOpacity="0.22" />
        </radialGradient>
      </defs>
      <g clipPath={`url(#c${uid})`}>
        <rect width="48" height="48" fill={a} />
        {pat === "stripes" && (
          <g fill={b}>
            {[2, 10, 18, 26, 34, 42].map((x) => (
              <rect key={x} x={x} y="0" width="4" height="48" />
            ))}
          </g>
        )}
        {pat === "stripes" && label && <circle cx="24" cy="24" r="15.5" fill={a} />}
        {pat === "sleeves" && (
          <circle cx="24" cy="24" r="20.5" fill="none" stroke={b} strokeWidth="5" />
        )}
        {pat === "plain" && (
          <circle cx="24" cy="24" r="19.5" fill="none" stroke={b} strokeOpacity="0.45" strokeWidth="1.2" />
        )}
        <rect width="48" height="48" fill={`url(#l${uid})`} />
      </g>
      <circle cx="24" cy="24" r="23" fill="none" stroke="#000" strokeOpacity="0.22" strokeWidth="1" />
      <circle cx="24" cy="24" r="22.2" fill="none" stroke="#fff" strokeOpacity="0.16" strokeWidth="0.8" />
      {label && code && (
        <text
          x="24"
          y="27.6"
          textAnchor="middle"
          fill={ink}
          style={{ font: "700 10.5px var(--font-sans)", letterSpacing: "0.03em", fontStretch: "105%" }}
        >
          {code}
        </text>
      )}
    </svg>
  );
}
