"""
Opponent model. Feed it every public event; ask it for smoothed tendencies.

Every estimate shrinks toward the population (all opponents pooled), and the population
shrinks toward PRIORS. So it is useful from hand 1, sharpens on the whole field within a
few hundred hands, and sharpens per opponent as that opponent's sample grows. If the
tournament hides opponent identities, the population layer still carries the edge.
"""
from collections import defaultdict

import eval7

import random

from .common import strength, strength_ref, to_cards

# Size buckets, as (chips to call) / (pot before the bet). 0: <40%  1: 40-80%  2: 80-125%  3: overbet/shove
BUCKET_EDGES = (0.4, 0.8, 1.25)

PRIORS = {
    "vpip": 0.30, "pfr": 0.14, "3bet": 0.06, "f3b": 0.50, "stealfold": 0.60, "jam": 0.02,
    "bet": 0.35,                                   # bets when checked to (postflop)
    "ftb": 0.45, "ftb0": 0.35, "ftb1": 0.45, "ftb2": 0.52, "ftb3": 0.60,   # fold to bet by size
    "ftr": 0.50,                                   # folds after betting and getting raised
    "rvb": 0.08,                                   # raises when facing a bet
    # showdown evidence on their bets, by size bucket: mean strength (equity vs a random
    # hand on the board where they bet) and share of bets made with < 50% equity
    "sdhs0": 0.66, "sdhs1": 0.72, "sdhs2": 0.77, "sdhs3": 0.80,
    "sdbluff0": 0.20, "sdbluff1": 0.15, "sdbluff2": 0.12, "sdbluff3": 0.10,
    "sdhs": 0.72, "sdbluff": 0.15,                 # same, all sizes pooled
}


def bucket(ratio):
    for i, edge in enumerate(BUCKET_EDGES):
        if ratio < edge:
            return i
    return len(BUCKET_EDGES)


def hand_strength(combo, board_cards, iters=None):
    """Strength of a specific hand on this board (0..1), same scale the bot uses live."""
    ref = strength_ref(board_cards)
    return strength(tuple(combo), board_cards, ref)


class Tracker:
    def __init__(self, k=20, k_pop=30, priors=None):
        self.k, self.k_pop = k, k_pop
        self.priors = dict(PRIORS)
        if priors:
            self.priors.update(priors)
        self.me = None
        self.c = defaultdict(lambda: [0.0, 0])     # (name, key) -> [sum, n]
        self.pop = defaultdict(lambda: [0.0, 0])   # key -> [sum, n]
        self.hands = defaultdict(int)
        self.showdowns = defaultdict(list)
        self.h = None

    # ------------------------------------------------------------------ queries
    def rate(self, name, key):
        pr = self.priors[key]
        px, pn = self.pop[key]
        pop = (px + self.k_pop * pr) / (pn + self.k_pop)
        x, n = self.c[(name, key)]
        return (x + self.k * pop) / (n + self.k)

    def pop_rate(self, key):
        px, pn = self.pop[key]
        return (px + self.k_pop * self.priors[key]) / (pn + self.k_pop)

    def fold_to_bet(self, name, b):
        """Fold rate facing a bet of size bucket b, using this player's overall fold tendency
        when their per-bucket sample is thin."""
        key = "ftb%d" % b
        pop_b = self.pop_rate(key)
        tilt = self.rate(name, "ftb") / max(0.05, self.pop_rate("ftb"))
        parent = min(0.97, max(0.02, pop_b * min(3.0, max(0.33, tilt))))
        x, n = self.c[(name, key)]
        return (x + self.k * parent) / (n + self.k)

    def showdown_rate(self, name, base, b):
        """Bet-size tell for bucket b ('sdhs' or 'sdbluff'). Shrinks to this player's own
        all-sizes average first, then to the population for that size, so one opponent's
        habits don't get averaged away by a noisy field."""
        key = "%s%d" % (base, b)
        pop_b = self.pop_rate(key)
        xa, na = self.c[(name, base)]
        player_all = (xa + self.k * pop_b) / (na + self.k)
        x, n = self.c[(name, key)]
        return (x + self.k * player_all) / (n + self.k)

    def n(self, name, key):
        return self.c[(name, key)][1]

    # ------------------------------------------------------------------ recording
    def _add(self, name, key, value):
        if name == self.me or name is None:
            return
        cell = self.c[(name, key)]
        cell[0] += value
        cell[1] += 1
        p = self.pop[key]
        p[0] += value
        p[1] += 1

    def observe(self, ev):
        t = ev.get("type")
        if t == "hand_start":
            self.me = ev.get("my_name", self.me)
            self.h = {"names": list(ev["seats"]), "positions": dict(zip(ev["seats"], ev["positions"])),
                      "stack": ev.get("stack", 10000), "pf_raises": 0, "opener": None,
                      "pf_callers_after_open": 0, "vpip": set(), "pfr": set(), "jam": set(),
                      "f3b_done": set(), "board": {}, "bets": defaultdict(list)}
            return
        h = self.h
        if h is None:
            return
        if t == "street":
            h["board"][ev["street"]] = list(ev["board"])
        elif t == "action":
            self._on_action(ev, h)
        elif t == "showdown":
            for name, cards in ev["shown"].items():
                if name == self.me:
                    continue
                self.showdowns[name].append((list(cards), list(ev["board"]), list(h["bets"].get(name, []))))
                if len(self.showdowns[name]) > 60:
                    self.showdowns[name].pop(0)
                for street, b in h["bets"].get(name, []):
                    board = h["board"].get(street)
                    if not board:
                        continue
                    hs = hand_strength(to_cards(cards), to_cards(board), 200)
                    self._add(name, "sdhs%d" % b, hs)
                    self._add(name, "sdbluff%d" % b, 1.0 if hs < 0.5 else 0.0)
                    self._add(name, "sdhs", hs)
                    self._add(name, "sdbluff", 1.0 if hs < 0.5 else 0.0)
        elif t == "hand_end":
            for name in h["names"]:
                if name == self.me:
                    continue
                self.hands[name] += 1
                self._add(name, "vpip", 1.0 if name in h["vpip"] else 0.0)
                self._add(name, "pfr", 1.0 if name in h["pfr"] else 0.0)
                self._add(name, "jam", 1.0 if name in h["jam"] else 0.0)
            self.h = None

    def _on_action(self, ev, h):
        a, name, street = ev["action"], ev["player"], ev["street"]
        if a == "blind":
            return
        if street == "preflop":
            if a in ("call", "raise"):
                h["vpip"].add(name)
            if h["pf_raises"] == 1 and name != h["opener"]:
                self._add(name, "3bet", 1.0 if a == "raise" else 0.0)
                pos = h["positions"].get(name)
                op = h["positions"].get(h["opener"])
                if pos in ("SB", "BB") and op in ("CO", "BTN", "SB") and h["pf_callers_after_open"] == 0:
                    self._add(name, "stealfold", 1.0 if a == "fold" else 0.0)
                if a == "call":
                    h["pf_callers_after_open"] += 1
            if h["pf_raises"] >= 2 and name == h["opener"] and name not in h["f3b_done"]:
                h["f3b_done"].add(name)
                self._add(name, "f3b", 1.0 if a == "fold" else 0.0)
            if a == "raise":
                h["pfr"].add(name)
                h["pf_raises"] += 1
                if h["pf_raises"] == 1:
                    h["opener"] = name
                if ev.get("allin") or ev["amount"] >= 0.3 * h["stack"]:
                    h["jam"].add(name)
            return
        to_call, pot = ev["to_call"], ev["pot"]
        if to_call == 0:
            self._add(name, "bet", 1.0 if a == "raise" else 0.0)
        else:
            fold = 1.0 if a == "fold" else 0.0
            if ev["bet_before"] > 0:
                self._add(name, "ftr", fold)
            else:
                b = bucket(to_call / float(max(1, pot - to_call)))
                self._add(name, "ftb%d" % b, fold)
                self._add(name, "ftb", fold)
            self._add(name, "rvb", 1.0 if a == "raise" else 0.0)
        if a == "raise":
            ratio = (ev["amount"] - ev["current_bet"]) / float(max(1, pot + ev["current_bet"] - ev["bet_before"]))
            h["bets"][name].append((street, bucket(ratio)))

    # ------------------------------------------------------------------ report
    def fitted_priors(self):
        """Population averages observed so far, ready to paste into PRIORS."""
        out = {}
        for key in self.priors:
            x, n = self.pop[key]
            out[key] = round(x / n, 3) if n >= 30 else self.priors[key]
        return out

    def report(self, names=None):
        names = names or sorted(self.hands, key=lambda x: -self.hands[x])
        cols = ["hands", "VPIP", "PFR", "3bet", "bet", "ftb<40", "ftb40-80", "ftb80-125", "ftbOver", "ftr",
                "sdHS sm/md/lg/ov", "bluff% sm/md/lg/ov"]
        rows = []
        for nm in names:
            r = self.rate
            rows.append([nm, self.hands[nm], r(nm, "vpip"), r(nm, "pfr"), r(nm, "3bet"), r(nm, "bet"),
                         self.fold_to_bet(nm, 0), self.fold_to_bet(nm, 1), self.fold_to_bet(nm, 2),
                         self.fold_to_bet(nm, 3), r(nm, "ftr"),
                         "/".join("%.2f" % self.showdown_rate(nm, "sdhs", b) for b in range(4)),
                         "/".join("%.0f" % (100 * self.showdown_rate(nm, "sdbluff", b)) for b in range(4))])
        p = self.pop_rate
        rows.append(["(population)", sum(self.hands.values()), p("vpip"), p("pfr"), p("3bet"), p("bet"),
                     p("ftb0"), p("ftb1"), p("ftb2"), p("ftb3"), p("ftr"),
                     "/".join("%.2f" % p("sdhs%d" % b) for b in range(4)),
                     "/".join("%.0f" % (100 * p("sdbluff%d" % b)) for b in range(4))])
        return cols, rows
