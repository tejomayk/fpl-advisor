import { Fragment } from "react";

/** Plain text with **bold** pairs. React escapes everything; no HTML is ever injected. */
export function Md({ text }: { text?: string | null }) {
  if (!text) return null;
  const parts = String(text).split(/\*\*(.+?)\*\*/g);
  return (
    <>
      {parts.map((p, i) =>
        i % 2 ? (
          <strong key={i} className="font-semibold text-fg">
            {p}
          </strong>
        ) : (
          <Fragment key={i}>{p}</Fragment>
        ),
      )}
    </>
  );
}
