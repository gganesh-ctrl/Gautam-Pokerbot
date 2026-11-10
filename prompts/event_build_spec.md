# Event-day build spec (Pittsburgh, Nov 12, 60 minutes)

Hand this to your AI assistant one stage at a time. Every stage ends with a bot that runs
and never makes an illegal move. Stop adding features at minute 50 no matter what.

Stage order follows the lab's ablation results (README): equity against estimated ranges
is worth the most, the opponent tracker next, EV-based sizing least. Build in that order so
that if you run out of time you've already banked the biggest gains.

The numbers below are the lab defaults. Re-tune them against your generated field and
update this file before the event. **Check the official rules first:** whether you may bring
notes or prompts, which libraries are installed, and whether AI assistants are allowed.

## Before you start: confirm with the organizers

- [ ] The wire protocol: message format, "raise to" vs "raise by", how all-in is expressed
- [ ] Time limit per action (and whether there's a time bank)
- [ ] Is `eval7` (or `treys`) installed? Can you `pip install`?
- [ ] Are opponents identified by a stable name/ID across hands? Does your process persist
      across all 10,000 games, or is it restarted between games?
- [ ] What happens on an illegal action or timeout (auto-fold? disqualification?)

If identities are hidden or the process restarts every game, the per-opponent part of
stage 3 can't work. Hard-code the population numbers at the bottom of this file instead.

## The 1 ms rule applies to every stage

Each action request gets 1 ms. Put this in your first prompt and keep it in every stage:

> Hard limit: 1 ms per decision. Target under 0.5 ms. Rules: never build an
> eval7.HandRange from a string inside a decision (it takes 0.75 ms); build one at startup
> and replace its .hands list. No Python Monte Carlo loops; use
> eval7.py_hand_vs_range_monte_carlo with ≤150 iterations (the exact routine on the river).
> Check the clock before each expensive step; past 0.5 ms, return check if free, call if
> the bet is ≤ 25% of the pot, else fold. Disable gc during decisions; gc.collect() between hands.

Time every stage with a local test before moving on.

## Stage 0 (minutes 0–10): legal bot

> Write a bot for this protocol: [paste the protocol]. It must always return a legal action.
> Wrap the decision in try/except; on any error return check if to_call == 0 else fold.
> Clamp every raise into [min_raise_to, max_raise_to]. For now: check when free, else call.

Run one hand against their harness. Don't move on until it completes cleanly.

## Stage 1 (10–20): preflop

> Add preflop logic. Hands ranked strongest first: [paste data/preflop_ranking.txt, or the
> top 60 rows of it]. "Top X%" means the hand's `top%` column ≤ X.
> - Unopened pot: raise to 3 BB (+1 BB per limper) with the top X% for our seat:
>   UTG 10, UTG1 11, MP 13, HJ 16, CO 22, BTN 34, SB 28. Otherwise check or fold.
>   With limpers in front, use max(X, 8) × 0.9.
> - Facing one raise: 3-bet to 3× (3.5× out of position, +1× per caller) with the top 3%;
>   call with the top 8% if the call is ≤ 12 BB; else fold.
> - Facing a 3-bet: 4-bet top 1.5% (shove if the bet is already > 20% of stacks);
>   call top 3.5% if the call is ≤ 12% of our stack; else fold.
> - Facing any raise that costs ≥ 30% of our stack: call if our equity vs the top 6% of
>   hands beats pot odds + 2%.

## Stage 2 (20–35): equity vs ranges (the biggest win)

> Postflop, estimate equity vs each live opponent's estimated range with eval7's C routine
> (150 iterations), and multiply the heads-up equities when several opponents are in.
> Build each range as a list of ~32 specific hands sampled from the ranking:
> - preflop raiser → top 14%; preflop caller → hands ranked between top 8% and top 30%;
>   big blind who checked → any hand
> - for every postflop action they've taken, score each hand's "strength" on the board at
>   that time: the share of 50 fixed random reference hands it beats (0.55 minimum for a
>   flush draw, 0.50 for an open-ended straight draw; compute the reference once per street). Then:
>   - they bet or raised → keep hands with strength ≥ 0.60 (≥ 0.65 if the bet was ≥ 80%
>     pot), plus weaker hands equal to 15% of what you kept, as bluffs
>   - they called → drop the weakest 25% of the list
>   - they checked → drop the strongest 25% of the list, but keep a fifth of those
> Decisions:
> - Heads-up, checked to us: bet 75% pot if equity ≥ 0.60, else check.
>   Facing a bet: call if equity × (pot + to_call) − to_call > 0; raise to 3× if equity ≥ 0.75.
> - Multiway: bet 2/3 pot (full pot if every opponent folds < 35% to bets) if equity ≥ 0.46
>   vs 2 opponents or ≥ 0.38 vs 3+. Facing a bet: raise pot if equity ≥ 0.62 (2 opp) /
>   0.52 (3+); call if equity ≥ pot odds + 0.04 per player still to act; else fold.

## Stage 3 (35–45): opponent tracker

> Track, per opponent and pooled across all opponents: VPIP, PFR, 3-bet %, fold rate when
> facing a postflop bet by size (<40%, 40–80%, 80–125%, >125% of pot), fold rate after
> betting and getting raised, and how often they bet when checked to. At showdown, for
> every bet they made, record their hand's strength on that street.
> Smooth each stat: pooled = (count + 30 × default) / (n + 30), then
> player = (count + 20 × pooled) / (n + 20).
> Use these to replace the fixed numbers in stages 1–2: the raiser's range is their top PFR%,
> the caller's is their VPIP%, the "called → drop" share is their fold rate for that size,
> and the strength threshold after a bet is (their average showdown strength for that size − 0.12).

## Stage 4 (45–50, only if stages 0–3 are solid): heads-up EV sizing

> Heads-up and checked to us: for bet sizes 33/50/75/100/150% pot and all-in, let f = their
> fold rate for that size bucket. Their calling range = the strongest (1 − f) of their range.
> EV(bet) = f × pot + (1 − f) × (equity vs calling range × (pot + 2 × bet) − bet).
> EV(check) = equity × pot × 0.95 in position (0.85 out of position, 1.0 on the river).
> Take the best. Bluffs appear automatically against players who over-fold.

## Minutes 50–60: freeze

> No new features. Run 200+ hands locally against check/call bots and random bots, with
> every decision timed. Fix crashes and anything over 1 ms only.

## Population numbers

Use as stage-3 defaults, or hard-code them if per-opponent tracking is impossible.
"Built-in field" is what the lab measured against `bots/field.py`; replace that column
with `python run.py --fit-priors` run against your generated field.

| stat | generic default | built-in field |
|---|---|---|
| VPIP / PFR / 3-bet | 0.30 / 0.14 / 0.06 | 0.22 / 0.10 / 0.04 |
| fold to bet <40% / 40–80% / 80–125% / >125% pot | 0.35 / 0.45 / 0.52 / 0.60 | 0.18 / 0.25 / 0.28 / 0.50 |
| fold after betting, facing a raise | 0.50 | 0.18 |
| bets when checked to | 0.35 | 0.33 |
| blinds fold to a late-position steal | 0.60 | 0.83 |

The pattern to remember: this kind of field folds to fewer than 1 in 3 normal-sized bets.
Value-bet big and thin, and almost never bluff, unless a specific opponent's fold rate says
otherwise.
