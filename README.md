# pokerlab: sparring lab for the No Limit pokerbot tournament

An 8-handed no-limit hold'em simulator built to the published format (blinds 50/100,
10,000-chip stacks reset every hand, "raise to" actions, strict dealer), a field of bots
modelled on what rushed AI-written entries look like, and a tuned exploitative bot to
beat them. Use it to decide what your 60-minute build should contain and which numbers
to type into it.

## Setup

```
pip install eval7          # C-speed hand evaluator; the only dependency
python tests/test_engine.py && python tests/test_bots.py
python run.py              # 2,000 hands, your bot vs the 7-bot field
```

Python 3.8+. Runs on a laptop: 10,000 hands takes about a minute per core.

## What's in the box

| path | what |
|---|---|
| `engine.py` | the dealer: blinds, betting rules, side pots, showdown, illegal-action and timeout auto-folds |
| `bots/exploit.py` | **your bot**: positional preflop, equity vs estimated ranges, opponent tracker, EV-based bet sizing. All tunable numbers live in `PARAMS` |
| `bots/tracker.py` | opponent model: per-player stats that shrink to a population average, size-bucketed fold rates, bet-size tells learned from showdowns |
| `bots/field.py` | 7 sparring bots: Chen+Monte Carlo, multiway Monte Carlo, pot-odds calling station, chart nit, maniac, "GTO-lite", and a buggy one |
| `bots/common.py` | cards, preflop tables, equity calculators, action helpers |
| `run.py` | simulations, duplicate mode, ablations, parameter sweeps, learned-reads report |
| `arena/` | the drag-and-drop battle page (`python3 arena/server.py`) |
| `tools/analyze_log.py` | where a bot wins and loses (position, street, heads-up vs multiway, biggest losing hands) |
| `tools/latency.py` | per-decision response times and timeouts under the 1 ms rule |
| `prompts/field_prompt.md` | prompts for generating realistic opponents with ChatGPT / Claude / Gemini |
| `prompts/event_build_spec.md` | the staged spec to hand your assistant at the event |
| `data/preflop_ranking.txt` | all 169 hands ranked for 8-handed play, with cumulative % |
| `bots/external/` | drop generated opponent bots here (`example_minimal.py` shows the shape) |
| `tools/build_preflop.py` | regenerates `data/preflop_equity.json` (already built) |

## The arena (drag-and-drop battles with friends)

```
python3 arena/server.py
```

Your browser opens the arena at http://localhost:8765. Drag bots from the rack onto the
8 seats (or press Seat), pick the number of hands, and press Run battle. You get standings
with confidence intervals, a chips-over-time chart, a replay of the biggest pots with every
hole card shown, and a history of past battles. The default rules match the event: 1 ms per
decision, strict auto-folds, stacks reset every hand.

**Adding a friend's bot:** drop their `.py` file onto the rack. It's saved to `bots/external/`
and appears in the rack. Dropping a file with the same name replaces the old version. A bot
needs one class with an `act(self, state)` method (see `bots/base.py`; `observe` is optional).

**Playing over the same Wi-Fi:** run `python3 arena/server.py --lan`. It prints an address
like `http://192.168.1.23:8765` that friends can open to upload their bots and watch.
Battles run on your computer.

Uploaded bots run as Python code on your machine with your permissions. Only add files from
people you trust, and only use `--lan` on a network you trust.

Each battle runs in its own process, so a crashing bot can't take the arena down, Stop ends a
battle immediately, and a bot stuck in a loop is interrupted after 250 ms and auto-folded.

## Commands

```
python run.py --hands 10000 --duplicate --workers 4      # trustworthy result
python run.py --report                                   # what your bot learned about each opponent
python run.py --hands 6000 --fit-priors                  # measure the field's tendencies -> data/fitted_priors.json
python run.py --param priors=data/fitted_priors.json     # start matches from those tendencies
python run.py --param use_tracker=0                      # switch a feature off
python run.py --sweep open_BTN=0.25,0.34,0.45 --duplicate --hands 8000 --workers 4
python run.py --lineup "exploit,exploit?use_tracker=0,chen_mc,station,nit,maniac,gto_lite,sloppy"
python run.py --lineup exploit,bots/external/gpt_1.py,...   # add generated bots
python run.py --anonymous            # opponents renamed every hand (tests the no-ID case)
python run.py --field 16             # 16 entrants, random 8 seated each hand
python run.py --illegal clamp        # forgiving dealer that clamps bad raises instead of folding
python run.py --hands 3000 --log hands.jsonl && python tools/analyze_log.py hands.jsonl exploit --worst 5
```

**Duplicate mode** deals each deck 8 times and rotates the seats, so every bot plays every
seat with the same cards. It removes most card luck. Use it for any comparison.

**Reading results.** `bb/100` is big blinds won per 100 hands; `±95%` is the confidence
half-width. Two settings differ only if the gap is clearly bigger than the intervals.
Runs are exactly repeatable for a given `--seed`, because the bot's 0.5 ms cutoff is
switched off by default; pass `--realtime` to switch it on (results then vary slightly run to run). Tune on one seed, then confirm
the winner on two or three fresh seeds before you believe it.

## Speed: the 1 ms rule

Every action request gets 1 ms. The engine enforces it by default (`--time-limit 0.001`);
a slower reply is auto-folded and shows in the timeout column.

`python tools/latency.py` measures your bot under that rule. Current numbers on a cloud vCPU:

| decision | median | 99th percentile |
|---|---|---|
| preflop | 0.01 ms | 0.07 ms |
| postflop heads-up | 0.24 ms | 0.48 ms |
| postflop multiway | 0.24 ms | 0.53 ms |

If a decision reaches 0.5 ms (`time_budget`), the bot stops and plays a safe fallback
(check, call small bets, fold big ones); that happens in under 1% of postflop decisions.
What makes it fast:
- **No range-string parsing per call.** `eval7.HandRange("...")` costs ~0.75 ms by itself;
  the bot reuses one object and swaps its hand list (~1 µs). This one fix was most of the gap.
- **No Python Monte Carlo.** Equity comes from eval7's C routine (~15 µs); multiway equity
  multiplies the heads-up equities against each opponent.
- **Cheap hand strength.** Opponent hands are scored by how many of 50 fixed reference hands
  they beat on the board (plus a floor for flush and open-ended draws), not by simulation.
- **Small, incremental ranges.** 32 sampled hands per opponent, narrowed only by actions
  that are new since the last decision.
- **No garbage collection during a decision.** Collection runs between hands.

Even bots that take 30 µs get an occasional timeout here (1–2 per 10,000 hands) from
operating-system hiccups, so keep the fallback in your event build.

## What the lab found (vs the built-in field)

10,000 hands per row, duplicate mode, seed 31, measured before the 1 ms rework. Every row is
the same bot with features switched off. The fast version scores +139 on the same seed and
+115 to +117 (1st each time) on three seeds with real-time budgets and the 1 ms rule on.

| build | bb/100 | finish | best other bot |
|---|---|---|---|
| full bot | **+143** | 1st | +82 |
| without EV sizing | +123 | 1st | +76 |
| without opponent tracker | +113 | 1st | +84 |
| stages 0–2 only (no tracker, no EV sizing) | +98 | 2nd | +104 |
| without range estimation (equity vs random hands) | +86 | 2nd | +88 |
| everything off: positional preflop + equity vs random | +91 | 2nd | +92 |

What that means for your 60 minutes:
- **A solid but standard bot ties the best AI-default bots.** The edge comes from the
  structure on top: equity against estimated ranges is the biggest piece, the tracker next,
  EV sizing last. That's the build order in `prompts/event_build_spec.md`.
- **The exact numbers barely matter.** Sweeping 13 parameters across sensible ranges
  (open sizes and ranges, 3-bet and call widths, multiway thresholds, realization factors,
  pool size, slowplay rate) moved results less than the ±40 bb/100 noise. Don't spend event
  minutes tuning; spend them on getting stages 2–3 working.
- **It holds up when conditions change.** Three fresh seeds: +139 / +157 / +114, 1st each
  time. Tougher field (2 nits, 2 GTO-lites, 2 multiway-MC, 1 Chen-MC): +63, 1st.
  Anonymous opponents (no per-player reads): +82 to +105, 1st. Random tables drawn from 16
  entrants: +108, 2nd behind a nit with overlapping intervals.
- **This field doesn't fold.** Against bets up to pot size it folds only 18–28% of the time
  (`--fit-priors`). Value-bet big and thin; bluff only players whose fold rate says so.
- **Bugs cost real money.** The `sloppy` bot is close to the Chen-MC bot plus three common
  bugs. It auto-folds about 1,000 times per 10,000 hands and finishes ~130 bb/100 behind it.

## Workflow before Nov 12

1. **Build the real field (Oct 28–30).** Follow `prompts/field_prompt.md`: 10–15 bots
   from different tools and prompt styles in `bots/external/`. This matters more than any
   tuning, because it's the closest thing to the actual room.
2. **Baseline and read the leaks (Oct 30–31).** Run your bot against them with
   `--report`. Read the generated code; note the patterns that repeat.
3. **Tune (Nov 1–4).** Sweep the `PARAMS` that matter (sweeps above show which), on
   one seed and on generated bots you haven't tuned against. Refit the tracker priors with
   `--fit-priors` so your bot starts every match with the field's real tendencies.
4. **Freeze the spec (Nov 5).** Copy the tuned numbers into `prompts/event_build_spec.md`.
5. **Rehearse twice under a 60-minute timer (Nov 6–10).** Build from the spec with an
   AI assistant in an empty folder, then drop the result into this lab
   (`--lineup exploit,yourbuild.py,...`). It should beat the field. Time each stage; cut
   whatever you can't finish by minute 50.
6. **Apply now.** Pittsburgh applications close Nov 9 at 11:59 PM ET, reviewed on a rolling
   basis with limited seats.

## Caveats

- **Format unknowns.** The announcement says "10,000 games, 8 bots each." That may be
  10,000 separate one-hand tables with random seating rather than one long session.
  If your process doesn't persist, or opponent IDs are hidden, per-opponent reads can't
  form; the population layer still works (`--anonymous` tests this). Ask the organizers.
- **The protocol is unknown.** Write a thin adapter from their messages to the `state`
  dict in `bots/base.py`; keep strategy code unchanged.
- **The built-in field is an approximation.** Results against it are a sanity check, not
  a forecast. Tuning hard against these 7 bots will overfit; tune against generated bots.
- **Variance is large.** Even 10,000 tournament hands leaves real luck in the final
  standings. This raises your odds; it can't guarantee a result.
