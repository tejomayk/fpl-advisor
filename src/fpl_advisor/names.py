"""Match player and club names from outside sources (bookmakers, news) to FPL ids."""

from __future__ import annotations

import re
import unicodedata

from .world import World

# Outside names for clubs that differ from FPL's `name` / `short_name`.
TEAM_ALIASES = {
    "manchester united": "man utd",
    "manchester city": "man city",
    "tottenham hotspur": "spurs",
    "tottenham": "spurs",
    "newcastle united": "newcastle",
    "nottingham forest": "nott'm forest",
    "nottm forest": "nott'm forest",
    "wolverhampton wanderers": "wolves",
    "wolverhampton": "wolves",
    "west ham united": "west ham",
    "brighton and hove albion": "brighton",
    "brighton & hove albion": "brighton",
    "afc bournemouth": "bournemouth",
    "leeds united": "leeds",
    "leicester city": "leicester",
    "ipswich town": "ipswich",
    "sunderland afc": "sunderland",
}


# Letters that Unicode doesn't split into a base letter plus an accent.
_SPECIAL = str.maketrans(
    {
        "ø": "o",
        "Ø": "O",
        "ł": "l",
        "Ł": "L",
        "ß": "ss",
        "æ": "ae",
        "Æ": "AE",
        "đ": "d",
        "ı": "i",
    }
)


def norm(s: str) -> str:
    """Lowercase, strip accents and punctuation: "Gabriel Magalhães" -> "gabriel magalhaes"."""
    s = (
        unicodedata.normalize("NFKD", s.translate(_SPECIAL))
        .encode("ascii", "ignore")
        .decode()
    )
    s = re.sub(r"[^a-z0-9' ]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def team_id(world: World, name: str) -> int | None:
    n = norm(name)
    # Exact name first: FPL sometimes spells a club out in full ("Ipswich Town").
    for key in dict.fromkeys((n, TEAM_ALIASES.get(n, n))):
        for t in world.teams.values():
            if key in (norm(t.name), norm(t.short)):
                return t.id
    return None


def player_id(world: World, name: str, team: int | None = None) -> int | None:
    """Best FPL id for a free-text player name, or None if it is ambiguous or unknown.

    Tries, in order: full name, FPL web name, then surname. A team narrows the search and is
    needed for common surnames.
    """
    n = norm(name)
    if not n:
        return None
    pool = [p for p in world.players.values() if team is None or p.team == team]
    for key in (
        lambda p: norm(p.full_name),
        lambda p: norm(p.name),
        lambda p: norm(p.full_name).split()[-1],
    ):
        hits = [p.id for p in pool if key(p) == n]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            return None
    # "Bruno Fernandes" vs FPL full name "Bruno Borges Fernandes": first and last words match.
    parts = n.split()
    if len(parts) >= 2:
        hits = [
            p.id
            for p in pool
            if (fn := norm(p.full_name).split())
            and fn[0] == parts[0]
            and fn[-1] == parts[-1]
        ]
        if len(hits) == 1:
            return hits[0]
    return None
