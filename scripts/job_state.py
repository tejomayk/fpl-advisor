#!/usr/bin/env python3
"""Guards for the scheduled jobs' state folder, which GitHub Actions keeps in its cache.

Standard library only. Used by .github/workflows/refresh.yml and elite.yml:

- `seq DIR`: print the spend ledger's sequence number (the highest of its two copies; 0 when
  there is no ledger yet). Taken right after the cache is restored.
- `check DIR --min-seq N`: exit 0 only if the folder is safe to save as the newest cache: every
  ledger copy that exists is readable or has a readable twin, and the ledger did not go
  backwards (its sequence number is at least N; it only ever grows, even on a reset). A run
  that broke the ledger must not replace the last good copy.
- `digest DIR`: a hash of every file in the folder, to skip saving a cache when nothing changed.

The ledger copies are FPL_USAGE_PATH and FPL_USAGE_MIRROR when set, else DIR/usage/usage.json
and DIR/usage.mirror/usage.json. Never prints file contents.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path


def ledger_copies(root: Path) -> list[Path]:
    main = Path(os.environ.get("FPL_USAGE_PATH") or root / "usage" / "usage.json")
    mirror = Path(
        os.environ.get("FPL_USAGE_MIRROR") or root / "usage.mirror" / "usage.json"
    )
    return [main, mirror]


def _seq(path: Path) -> int | None:
    """The copy's sequence number, or None if it is missing or unreadable."""
    try:
        d = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or not isinstance(d.get("seq", 0), int):
        return None
    if not isinstance(d.get("providers", {}), dict):
        return None
    return d.get("seq", 0)


def ledger_seq(root: Path) -> int:
    return max([s for s in map(_seq, ledger_copies(root)) if s is not None] or [0])


def problems(root: Path, min_seq: int) -> list[str]:
    copies = ledger_copies(root)
    present = [p for p in copies if p.exists()]
    good = [s for s in map(_seq, present) if s is not None]
    if not present:
        return ["the spend ledger disappeared"] if min_seq > 0 else []
    if not good:
        return ["no readable copy of the spend ledger"]
    if max(good) < min_seq:
        return [f"the spend ledger went backwards ({max(good)} < {min_seq})"]
    return []


def digest(root: Path) -> str:
    h = hashlib.sha256()
    if root.is_dir():
        for p in sorted(root.rglob("*")):
            if p.is_file() and not p.name.endswith(".tmp"):
                h.update(p.relative_to(root).as_posix().encode() + b"\0")
                h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("seq", "check", "digest"):
        p = sub.add_parser(name)
        p.add_argument("dir", type=Path)
        if name == "check":
            p.add_argument("--min-seq", type=int, default=0)
    args = ap.parse_args(argv)
    if args.cmd == "seq":
        print(ledger_seq(args.dir))
        return 0
    if args.cmd == "digest":
        print(digest(args.dir))
        return 0
    found = problems(args.dir, args.min_seq)
    for msg in found:
        print(f"job state not saved: {msg}", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
