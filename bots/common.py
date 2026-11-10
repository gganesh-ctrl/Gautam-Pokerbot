"""Shared helpers: cards, preflop tables, hand percentiles, equity calculators."""
import json
import os
from bisect import bisect_left, bisect_right
import random
import time

import eval7

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")

RANKS = "23456789TJQKA"
RANK_VAL = {r: i + 2 for i, r in enumerate(RANKS)}
CARD = {r + s: eval7.Card(r + s) for r in RANKS for s in "cdhs"}
ALL_CARDS = list(CARD.values())
RANDOM_RANGE = eval7.HandRange(
    "22+,A2s+,K2s+,Q2s+,J2s+,T2s+,92s+,82s+,72s+,62s+,52s+,42s+,32s,"
    "A2o+,K2o+,Q2o+,J2o+,T2o+,92o+,82o+,72o+,62o+,52o+,42o+,32o")


def to_cards(strs):
    return [CARD[c] for c in strs]


def hand_class(hole):
    """['As','Kd'] -> 'AKo', ['7h','7d'] -> '77', ['Jc','Tc'] -> 'JTs'."""
    a, b = hole[0], hole[1]
    if RANK_VAL[a[0]] < RANK_VAL[b[0]]:
        a, b = b, a
    if a[0] == b[0]:
        return a[0] + b[0]
    return a[0] + b[0] + ("s" if a[1] == b[1] else "o")


def class_combos(cls):
    hi, lo = cls[0], cls[1]
    out = []
    if len(cls) == 2:
        suits = "cdhs"
        for i in range(4):
            for j in range(i + 1, 4):
                out.append((CARD[hi + suits[i]], CARD[lo + suits[j]]))
    elif cls[2] == "s":
        for s in "cdhs":
            out.append((CARD[hi + s], CARD[lo + s]))
    else:
        for s1 in "cdhs":
            for s2 in "cdhs":
                if s1 != s2:
                    out.append((CARD[hi + s1], CARD[lo + s2]))
    return out


# ---------------------------------------------------------------- preflop tables
with open(os.path.join(DATA, "preflop_equity.json")) as _f:
    PREFLOP_EQ = json.load(_f)          # class -> [eq vs 1 random, vs 2, ..., vs 7]
CLASSES = list(PREFLOP_EQ)


def preflop_equity(hole, n_opps):
    return PREFLOP_EQ[hand_class(hole)][min(max(n_opps, 1), 7) - 1]


def _percentile_table(key):
    """class -> fraction of all 1326 combos that are at least as strong (AA ~ 0.005, 72o ~ 1.0)."""
    order = sorted(CLASSES, key=key, reverse=True)
    out, cum = {}, 0
    for c in order:
        cum += len(class_combos(c))
        out[c] = cum / 1326.0
    return out, order


# Heads-up ranking (what equity-vs-random bots implicitly use) and a multiway ranking
# (equity vs 3 random hands, which favours pairs and suited connectors).
PCT_HU, ORDER_HU = _percentile_table(lambda c: PREFLOP_EQ[c][0])
PCT_MW, ORDER_MW = _percentile_table(lambda c: 0.5 * PREFLOP_EQ[c][2] * 4 + 0.5 * PREFLOP_EQ[c][0])
SORTED_COMBOS_HU = [combo for c in ORDER_HU for combo in class_combos(c)]


def hand_pct(hole, table=None):
    return (table or PCT_MW)[hand_class(hole)]


def top_range_combos(frac, dead, lo=0.0):
    """Combos in the band (lo, frac] of the heads-up ranking, minus dead cards."""
    n_hi = max(1, int(round(frac * 1326)))
    n_lo = int(round(lo * 1326))
    dead = set(dead)
    return [c for c in SORTED_COMBOS_HU[n_lo:n_hi] if c[0] not in dead and c[1] not in dead]


def chen(hole):
    """Bill Chen's formula, the preflop heuristic most AI-written bots reach for first."""
    pts = {"A": 10, "K": 8, "Q": 7, "J": 6, "T": 5}
    a, b = hole
    ra, rb = RANK_VAL[a[0]], RANK_VAL[b[0]]
    if ra < rb:
        a, b, ra, rb = b, a, rb, ra
    score = pts.get(a[0], ra / 2.0)
    if ra == rb:
        return max(5, score * 2)
    if a[1] == b[1]:
        score += 2
    gap = ra - rb - 1
    score -= {0: 0, 1: 1, 2: 2, 3: 4}.get(gap, 5)
    if gap <= 1 and ra < RANK_VAL["Q"]:
        score += 1
    return score


# ---------------------------------------------------------------- equity
def equity_vs_random(hole, board, iters=400):
    """Equity vs ONE random hand (C speed). This is what most quick bots compute."""
    return eval7.py_hand_vs_range_monte_carlo(to_cards(hole), RANDOM_RANGE, to_cards(board), iters)


# One reusable range object. Parsing a range string costs ~0.75 ms, which alone would blow a
# 1 ms decision budget; swapping the hand list on an existing object costs ~1 us.
_HR = eval7.HandRange("AA")


def equity_vs_range(hole_cards, combos, board_cards, iters=150):
    """Equity vs one opponent holding a hand from `combos` (list of (Card, Card)).
    C speed: ~10-20 us for 150 iterations, exact on the river."""
    if not combos:
        return 0.5
    _HR.hands = [(c, 1.0) for c in combos]
    if len(board_cards) == 5:
        return eval7.py_hand_vs_range_exact(hole_cards, _HR, board_cards)
    return eval7.py_hand_vs_range_monte_carlo(hole_cards, _HR, board_cards, iters)


# ---------------------------------------------------------------- fast hand strength
_INFO = {CARD[r + s]: (RANK_VAL[r], s) for r in RANKS for s in "cdhs"}


_REF_RNG = random.Random(12345)
_REF_COMBOS = [tuple(_REF_RNG.sample(ALL_CARDS, 2)) for _ in range(160)]   # fixed reference hands


def strength_ref(board_cards, rng=None, n=70):
    """Precomputed reference for strength() on one board: values of ~n fixed random hands,
    plus the board's suit counts and ranks. Build once per street (~60 us)."""
    dead = set(board_cards)
    vals = []
    for a, b in _REF_COMBOS:
        if a in dead or b in dead:
            continue
        vals.append(eval7.evaluate([a, b] + board_cards))
        if len(vals) >= n:
            break
    vals.sort()
    suits = {}
    for c in board_cards:
        s = _INFO[c][1]
        suits[s] = suits.get(s, 0) + 1
    ranks = {_INFO[c][0] for c in board_cards}
    return vals, suits, ranks, len(board_cards) < 5


def strength(combo, board_cards, ref):
    """0..1: share of random hands this hand beats right now on this board, with a floor for
    strong draws on the flop and turn. ~2 us. Stands in for 'equity vs a random hand'."""
    vals, suits, branks, drawing = ref
    v = eval7.evaluate([combo[0], combo[1]] + board_cards)
    lo, hi = bisect_left(vals, v), bisect_right(vals, v)
    pct = (lo + 0.5 * (hi - lo)) / len(vals)
    if drawing and pct < 0.55:
        (r1, s1), (r2, s2) = _INFO[combo[0]], _INFO[combo[1]]
        if suits.get(s1, 0) + (2 if s1 == s2 else 1) >= 4 or (s1 != s2 and suits.get(s2, 0) + 1 >= 4):
            return 0.55                                             # flush draw
        ranks = branks | {r1, r2}
        for lo_r in (r1 - 3, r1 - 2, r1 - 1, r1, r2 - 3, r2 - 2, r2 - 1, r2):
            if 2 <= lo_r <= 10 and lo_r in ranks and lo_r + 1 in ranks and lo_r + 2 in ranks and lo_r + 3 in ranks:
                return max(pct, 0.5)                                # open-ended straight draw
    return pct


def mc_equity_multi(hole_cards, board_cards, opp_pools, iters=300, deadline=None, rng=random):
    """
    Equity vs several opponents at once. opp_pools: one list of (Card, Card) per opponent,
    or None for a random hand. Python speed (~10 us per iteration per 3 opponents).
    """
    dead = set(hole_cards) | set(board_cards)
    deck = [c for c in ALL_CARDS if c not in dead]
    need = 5 - len(board_cards)
    score, done = 0.0, 0
    for i in range(iters):
        if deadline is not None and (i & 31) == 0 and i > 0 and time.perf_counter() > deadline:
            break
        used = set()
        opp_hands = []
        ok = True
        for pool in opp_pools:
            if pool:
                for _ in range(8):
                    h = pool[rng.randrange(len(pool))]
                    if h[0] not in used and h[1] not in used:
                        break
                else:
                    ok = False
                    break
            else:
                while True:
                    h = (deck[rng.randrange(len(deck))], deck[rng.randrange(len(deck))])
                    if h[0] is not h[1] and h[0] not in used and h[1] not in used:
                        break
            used.add(h[0]); used.add(h[1])
            opp_hands.append(h)
        if not ok:
            continue
        runout = []
        while len(runout) < need:
            c = deck[rng.randrange(len(deck))]
            if c not in used:
                used.add(c)
                runout.append(c)
        b = board_cards + runout
        me = eval7.evaluate(hole_cards + b)
        best, ties = 0, 0
        for h in opp_hands:
            v = eval7.evaluate([h[0], h[1]] + b)
            if v > best:
                best, ties = v, 1
            elif v == best:
                ties += 1
        if me > best:
            score += 1
        elif me == best:
            score += 1.0 / (ties + 1)
        done += 1
    return score / done if done else 0.5


def pot_odds(to_call, pot):
    return to_call / float(pot + to_call) if to_call > 0 else 0.0


# ---------------------------------------------------------------- action helpers
def check_or_fold(state):
    return "check" if state["to_call"] == 0 else "fold"


def check_or_call(state):
    return "check" if state["to_call"] == 0 else "call"


def raise_to(state, amount):
    """Legal 'raise to' clamped into [min_raise_to, max_raise_to]; falls back to call/check."""
    if "raise" not in state["legal"]:
        return check_or_call(state)
    amount = int(max(state["min_raise_to"], min(int(amount), state["max_raise_to"])))
    return ("raise", amount)


def live_opponents(state):
    return [p for p in state["players"] if not p["folded"] and p["seat"] != state["my_seat"]]
