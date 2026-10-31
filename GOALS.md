# Goals

## Milestones
- [x] M1: data layer + projection model + backtest beating PPG baseline
  - Evidence (GW3-8, 2026/27, out of sample): corr 0.378 vs 0.265 (PPG so far) / 0.198 (last-season PPG);
    top-30 picks 4.11 vs 3.61 actual pts; calibrated within ~0.3 pts per bin where n > 90.
- [x] M2: optimiser (transfers, captain, XI, chips) with tests
- [x] M3: league view, adaptive risk, points target, price alerts
- [x] M4: CLI, config, ntfy alerts, GitHub Actions workflow
- [x] M5a: fresh-context review: 12 of 14 findings fixed and tested (194 tests). Raw model MAE 1.85.
- [x] M6: hosted one-page website (FastAPI + vanilla JS), alert sign-up with hourly scheduler, Docker
  packaging; security review: 15 findings fixed (proxy-aware rate limits, season-end expiry, stale-while-
  refresh FPL data, token out of URLs, CSP, cache pruning, ntfy limits). 245 tests.
- [x] M8: outside signals: elite managers (100 best median ranks over 3 seasons, from a 50k scan),
  bookmaker odds (The Odds API, ~300 of 500 free credits/month), LLM team news (OpenRouter).
  Evidence: elite backtest GW3-8, beta 2: top-30 4.11 -> 4.46, corr 0.378 -> 0.381; global
  ownership adds nothing. Used at beta 1 (leakage). Odds/news untested (no free history); weights 0.6/0.5.
- [ ] M7: public GitHub repo + deployment (needs owner's choice of host and go-ahead)

## Decisions
- 2026-10-30: Open source, generic for any manager (no personal data in repo). MIT license.
- 2026-10-30: Python + uv, PuLP 2.x (bundled CBC; PuLP 4 ships no solver).
- 2026-10-30: Alerts via ntfy (free, open source, iOS/Android, no account); scheduling via GitHub Actions cron.
- 2026-10-30: Team strength comes from xG + goals in finished fixtures (FPL's strength fields are 0 this season).
- 2026-10-30: Captain picked on raw expected points; the rival-ownership weighting only affects squad
  selection, and is gentle (lambda capped at 0.2 when chasing, -0.12 when leading).
- 2026-10-30: Product is a hosted single-page website (owner's decision); CLI/GitHub Actions kept for self-hosters.
- 2026-10-30: Alerts bundled (one news alert, one price alert) to avoid notification spam.
- 2026-10-30: Free transfers are kept but not topped up in a Wildcard/Free Hit week (PL's own example).
- 2026-10-30: Points target = best rival's projected final with their edge over the FPL average shrunk
  (15-GW shrink) toward a 52-pt long-run average, plus 10.

## Next ideas
- Elite ranking: median of 3 lets in one-disaster managers (e.g. 11 / 1,904 / 911,206); consider worst-two-of-three.
- Re-run scripts/elite_backtest.py as more gameweeks finish; drop or raise ELITE_WEIGHT accordingly.
- Start rate counts injury absences as rotation (e.g. a player back from injury looks rotated);
  use FPL news history or ignore 0-minute rows during a flagged absence.
- Purchase price for managers who joined mid-season uses the match-day price (can be off by £0.1m).
- Multi-week transfer planning (plan the next 2-3 gameweeks' transfers jointly).
- Use FPL's set-piece/penalty order in the model.
- A hosted web page (needs a small proxy because FPL's API blocks browser calls).

## Blockers
- Team news needs a source whose terms allow AI processing (BBC, Guardian, Premier League all restrict it);
  owner to decide. Needs OPENROUTER_API_KEY.
- Odds need ODDS_API_KEY (free tier) from the owner; prop formats/coverage unverified until then.
