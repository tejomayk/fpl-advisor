import { AnimatePresence, m } from "motion/react";
import { useEffect, useId, useState, type FormEvent } from "react";
import { looksLikeLeagueLink, looksLikeTeamLink, parseIds } from "../lib/ids";

interface Props {
  initialTeam?: string;
  initialLeague?: string;
  initialErrors?: { team?: string; league?: string };
  busy?: boolean;
  onSubmit: (team: number, league: number | null) => void;
  /** Called once the visitor stops typing on a valid ID, to start the request early. */
  onPause?: (team: number, league: number | null) => void;
  autoFocus?: boolean;
}

const PAUSE_MS = 700; // "finished typing": no change in either box for this long

function Field({
  id,
  label,
  value,
  onChange,
  error,
  placeholder,
  autoFocus,
  optional,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (v: string) => void;
  error?: string;
  placeholder: string;
  autoFocus?: boolean;
  optional?: boolean;
}) {
  return (
    <div>
      <label htmlFor={id} className="mb-2 flex items-baseline justify-between text-sm font-medium text-fg-2">
        {label}
        {optional && <span className="text-[13px] font-normal text-fg-3">Optional</span>}
      </label>
      <input
        id={id}
        type="text"
        inputMode="text"
        autoComplete="off"
        autoCapitalize="off"
        spellCheck={false}
        autoFocus={autoFocus}
        value={value}
        placeholder={placeholder}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-err` : undefined}
        onChange={(e) => onChange(e.target.value)}
        className={`h-14 w-full rounded-2xl bg-raised px-5 text-[17px] text-fg ring-1 transition-[box-shadow,background] outline-none ring-inset placeholder:text-fg-3 focus:bg-panel focus:ring-2 focus:ring-grass ${
          error ? "ring-red" : "ring-transparent"
        }`}
      />
      <AnimatePresence initial={false}>
        {error && (
          <m.p
            id={`${id}-err`}
            role="alert"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="overflow-hidden text-sm text-red"
          >
            <span className="block pt-2">{error}</span>
          </m.p>
        )}
      </AnimatePresence>
    </div>
  );
}

export function Finder({ initialTeam = "", initialLeague = "", initialErrors = {}, busy, onSubmit, onPause, autoFocus }: Props) {
  const uid = useId();
  const [team, setTeam] = useState(initialTeam);
  const [league, setLeague] = useState(initialLeague);
  const [errors, setErrors] = useState(initialErrors);
  const [help, setHelp] = useState(false);

  useEffect(() => {
    if (!onPause) return;
    const r = parseIds(team, league);
    if (!r.team || r.errors.team || r.errors.league) return;
    const t = window.setTimeout(() => onPause(r.team!, r.league), PAUSE_MS);
    return () => clearTimeout(t);
  }, [team, league, onPause]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const r = parseIds(team, league);
    let t = team;
    let l = league;
    if (r.league && !r.errors.league) l = String(r.league);
    if (r.team) t = String(r.team);
    else if (looksLikeLeagueLink(team)) t = ""; // a league link in the team box: moved below
    if (!r.league && looksLikeTeamLink(league)) l = ""; // a team link in the league box: moved up
    setTeam(t);
    setLeague(l);
    setErrors(r.errors);
    if (r.errors.team) return document.getElementById(`${uid}-team`)?.focus();
    if (r.errors.league) return document.getElementById(`${uid}-league`)?.focus();
    if (r.team) onSubmit(r.team, r.league);
  };

  return (
    <form onSubmit={submit} noValidate className="flex flex-col gap-5">
      <Field
        id={`${uid}-team`}
        label="Team ID or FPL link"
        value={team}
        onChange={setTeam}
        error={errors.team}
        placeholder="1234567"
        autoFocus={autoFocus}
      />
      <Field
        id={`${uid}-league`}
        label="Mini-league ID or link"
        value={league}
        onChange={setLeague}
        error={errors.league}
        placeholder="76543"
        optional
      />
      <m.button
        type="submit"
        disabled={busy}
        whileHover={{ y: -1 }}
        whileTap={{ scale: 0.98 }}
        transition={{ type: "spring", stiffness: 500, damping: 30 }}
        className="mt-2 h-14 rounded-2xl bg-grass text-[17px] font-semibold text-on-grass disabled:opacity-60"
      >
        {busy ? "Loading…" : "Get my advice"}
      </m.button>
      <div>
        <button
          type="button"
          aria-expanded={help}
          onClick={() => setHelp((h) => !h)}
          className="flex items-center gap-1.5 text-sm text-fg-2 transition-colors hover:text-fg"
        >
          Where do I find these?
          <m.svg animate={{ rotate: help ? 180 : 0 }} viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
            <path d="M6 9l6 6 6-6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </m.svg>
        </button>
        <AnimatePresence initial={false}>
          {help && (
            <m.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              transition={{ type: "spring", stiffness: 400, damping: 40 }}
              className="overflow-hidden"
            >
              <div className="space-y-3 pt-4 text-[15px] leading-relaxed text-fg-2">
                <p>
                  <span className="font-medium text-fg">Team.</span> On the FPL site, open Points. The link contains{" "}
                  <code className="num rounded bg-raised px-1.5 py-0.5 text-[13px] text-fg">/entry/1234567/</code>. Paste the
                  link or the number.
                </p>
                <p>
                  <span className="font-medium text-fg">League.</span> Open your league's standings. The link contains{" "}
                  <code className="num rounded bg-raised px-1.5 py-0.5 text-[13px] text-fg">/leagues/76543/</code>.
                </p>
              </div>
            </m.div>
          )}
        </AnimatePresence>
      </div>
    </form>
  );
}
