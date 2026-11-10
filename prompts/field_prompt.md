# Generating a realistic field

Your real opponents are bots that ChatGPT, Claude, Gemini and Cursor wrote in under an hour.
The best sparring partners are therefore bots *you* get those same tools to write, prompted
the way a rushed competitor would prompt them. The built-in field (`bots/field.py`) is a
starting approximation; replace it with the real thing.

## How to do it

1. Open a fresh chat in each assistant (no custom instructions, no memory of this project).
2. Paste **one** of the competitor prompts below, followed by the **interface block**.
3. Save the code it returns as `bots/external/<tool>_<n>.py`. Don't fix its strategy.
   Only fix import errors, so you keep the logic bugs real competitors will have.
4. Run it in the lineup:
   ```
   python run.py --hands 4000 --duplicate --lineup exploit,bots/external/gpt_1.py,bots/external/gemini_1.py,chen_mc,station,nit,maniac,gto_lite
   ```
5. Aim for 10–15 external bots across tools and prompt styles, then rotate them through
   lineups with `--field` or several `--lineup` runs.

Allowed imports for generated bots: Python standard library, `eval7`, and `bots.common`
helpers if you want (most competitors won't have those, so prefer stdlib + eval7).

## Competitor prompts (one per chat)

Use each with 2–3 different tools. The variety matters more than the count.

**A. The minimal prompt**
> I'm in a 60 minute poker bot competition, 8-handed no-limit hold'em, blinds 50/100,
> 10,000 chip stacks that reset every hand. Write me a bot that wins. Python.

**B. The "make it GTO" prompt**
> Write a GTO poker bot for 8-player no-limit hold'em in Python. It should be hard to
> exploit. Stacks are 100 big blinds and reset every hand.

**C. The aggressive prompt**
> Write an aggressive no-limit hold'em bot that puts pressure on other bots and bluffs
> when they're weak. 8 players, 100bb, Python.

**D. The math-student prompt**
> Write a poker bot that uses Monte Carlo simulation to estimate equity and makes
> decisions with pot odds and expected value. 8-handed NLHE, Python, must respond fast.

**E. The "CFR" prompt**
> Implement a counterfactual regret minimization poker bot for 8-player no-limit
> hold'em that I can build in under an hour. Python.

**F. The iterate prompt** (two messages)
> Write a no-limit hold'em bot for an 8-player bot tournament. Python.

then, after it answers:
> Make it better. Add opponent modeling and adjust bet sizing.

## Interface block (paste after the competitor prompt)

```
Use this exact interface. Return only the code.
Each call to act() must return within 1 millisecond or the dealer folds your hand.

class MyBot:
    def __init__(self, seed=None): ...
    def observe(self, event: dict) -> None: ...   # optional: public events
    def act(self, state: dict):                     # required

state keys:
  street: 'preflop' | 'flop' | 'turn' | 'river'
  hole: ['As', 'Kd']            board: ['Qh', '7c', '2s']   (rank + suit, T for ten)
  position: 'UTG','UTG1','MP','HJ','CO','BTN','SB','BB'
  pot: chips in the middle, including all bets this street
  to_call: chips needed to call      current_bet: highest total bet this street
  my_bet: my total bet this street   my_stack: my chips behind
  min_raise_to / max_raise_to: legal raise-to window (None if raising is not allowed)
  legal: subset of ['fold', 'check', 'call', 'raise']
  players: list of {seat, name, position, stack, bet, committed, folded, allin}
  history: list of this hand's public events so far (dicts with 'type', 'player', 'action', 'amount', ...)
  bb, sb, starting_stack (10000), n_live

act must return one of:
  'fold' | 'check' | 'call' | ('raise', raise_to_total) | 'allin'
Raise amounts are "raise to" totals for this street, not increments.

events passed to observe: {'type': 'hand_start' | 'action' | 'street' | 'showdown' | 'hand_end', ...}
Available libraries: Python standard library and eval7 (eval7.Card('As'), eval7.evaluate(list_of_cards)
returns an int where higher is better, eval7.HandRange, eval7.py_hand_vs_range_monte_carlo).
```

## What to look for when you read the generated code

These are the leaks you're tuning against. Note which ones you see most often; that's
your read on the real room.

- Equity computed vs **one random hand** even when 5 players are in the pot
- Calling whenever equity beats pot odds, so it never folds a pair to a big bet
- Bet size that grows with hand strength (big bet = strong, small = medium)
- Fixed thresholds that never adapt to opponents
- Preflop by Chen formula or a short hard-coded chart
- "Raise by" vs "raise to" confusion, missing `T` in a rank map
- Too slow for 1 ms: Python Monte Carlo loops, or `eval7.HandRange("...")` parsed every decision
  (run them with the default `--time-limit 0.001` and watch the timeout column)
