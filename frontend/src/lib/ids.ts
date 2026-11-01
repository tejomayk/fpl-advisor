// Team / league ID parsing for the finder. Mirrors the rules of the previous front end:
// a plain number, or an FPL link containing /entry/<id>/ or /leagues/<id>/, in either box.
const ENTRY_RE = /\/entry\/(\d{1,8})(?!\d)/i;
const LEAGUE_RE = /\/leagues\/(\d{1,8})(?!\d)/i;
const PLAIN_RE = /^#?(\d{1,8})$/;

export const looksLikeLeagueLink = (s: string) => LEAGUE_RE.test(s);
export const looksLikeTeamLink = (s: string) => ENTRY_RE.test(s);

export interface Parsed {
  team: number | null;
  league: number | null;
  errors: { team?: string; league?: string };
}

export function parseIds(teamRaw: string, leagueRaw: string): Parsed {
  const t = String(teamRaw || "").trim();
  const l = String(leagueRaw || "").trim();
  // IDs are positive whole numbers: "0" and "000" count as not found.
  const pos = (v?: string) => (v && Number(v) > 0 ? v : undefined);
  const plain = (s: string) => pos((s.replace(/[\s,.]/g, "").match(PLAIN_RE) || [])[1]);
  const team = pos(t.match(ENTRY_RE)?.[1]) ?? plain(t) ?? pos(l.match(ENTRY_RE)?.[1]);
  const league = pos(l.match(LEAGUE_RE)?.[1]) ?? plain(l) ?? pos(t.match(LEAGUE_RE)?.[1]);
  const errors: Parsed["errors"] = {};
  if (!team) {
    if (!t) errors.team = "Enter your team ID or paste your FPL Points link.";
    else if (LEAGUE_RE.test(t))
      errors.team = "That's a league link, so it's moved below. Now add your team: paste your Points page link.";
    else errors.team = "No team ID in that. Type the number, or paste a link containing /entry/.";
  }
  if (l && !league && !ENTRY_RE.test(l)) {
    errors.league = "No league ID in that. Type the number, or paste a link containing /leagues/.";
  }
  return { team: team ? Number(team) : null, league: league ? Number(league) : null, errors };
}
