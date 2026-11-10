"""
The sparring field: what rushed, AI-written tournament bots tend to look like.

Each class is modelled on the code an assistant produces from a short prompt under time
pressure. They are deliberately ordinary, not strawmen: several play a reasonable
"textbook" game. Replace or extend them with bots you generate yourself (see
prompts/field_prompt.md); that is the best approximation of the real room.
"""
import random

from .base import Bot
from .common import (PCT_HU, chen, check_or_call, check_or_fold, equity_vs_random,
                     hand_class, live_opponents, mc_equity_multi, pot_odds,
                     preflop_equity, raise_to, to_cards, RANK_VAL)
import eval7


class ChenMC(Bot):
    """Prompt: 'write a no-limit hold'em bot'. Chen formula preflop, Monte Carlo vs ONE random
    hand postflop, bet size grows with equity (a sizing tell), ignores the number of opponents."""
    name = "chen_mc"

    def act(self, s):
        bb, to_call, pot = s["bb"], s["to_call"], s["pot"]
        if s["street"] == "preflop":
            score = chen(s["hole"])
            if score >= 10:
                if s["current_bet"] <= bb:
                    return raise_to(s, 3 * bb)
                if s["current_bet"] <= 4 * bb:
                    return raise_to(s, 3 * s["current_bet"])
                if score >= 12 or to_call <= 0.3 * s["my_stack"]:
                    return "call"
                return "fold"
            if score >= 7 and to_call <= 3 * bb:
                return check_or_call(s)
            if score >= 5 and to_call <= bb and s["position"] in ("CO", "BTN", "SB", "BB"):
                return check_or_call(s)
            return check_or_fold(s)
        eq = equity_vs_random(s["hole"], s["board"], 400)
        if eq > 0.85:
            return raise_to(s, s["current_bet"] + pot)
        if eq > 0.7 and (to_call == 0 or eq > 0.78):
            return raise_to(s, s["current_bet"] + int(0.6 * pot))
        if to_call == 0:
            return "check"
        return "call" if eq > pot_odds(to_call, pot) else "fold"


class MultiwayMC(Bot):
    """Prompt: 'write a strong poker bot, account for multiple opponents'. Equity vs every live
    opponent (random hands), fixed 2/3-pot sizing, small margin over pot odds."""
    name = "mc_multi"

    def act(self, s):
        bb, to_call, pot = s["bb"], s["to_call"], s["pot"]
        n_opp = max(1, len(live_opponents(s)))
        fair = 1.0 / (n_opp + 1)
        if s["street"] == "preflop":
            eq = preflop_equity(s["hole"], n_opp)
            if eq > fair * 1.8:
                if s["current_bet"] <= bb:
                    return raise_to(s, 3 * bb)
                return raise_to(s, 3 * s["current_bet"]) if eq > fair * 2.4 else "call"
            if to_call == 0:
                return "check"
            if eq > pot_odds(to_call, pot) + 0.05 and eq > fair * 1.1:
                return "call"
            return "fold"
        eq = mc_equity_multi(to_cards(s["hole"]), to_cards(s["board"]), [None] * n_opp, 60)
        if eq > max(0.6, 2.0 * fair):
            if to_call == 0:
                return raise_to(s, int(0.66 * pot))
            return raise_to(s, s["current_bet"] * 2 + int(0.5 * pot))
        if to_call == 0:
            return "check"
        return "call" if eq > pot_odds(to_call, pot) + 0.03 else "fold"


class PotOddsStation(Bot):
    """Prompt: 'make a mathematically correct bot using pot odds'. Calls whenever equity vs a
    random hand beats the price. Rarely bets, never bluffs, almost never folds a pair."""
    name = "station"

    def act(self, s):
        bb, to_call, pot = s["bb"], s["to_call"], s["pot"]
        if s["street"] == "preflop":
            score = chen(s["hole"])
            if score >= 11:
                return raise_to(s, max(3 * bb, 3 * s["current_bet"]))
            if score >= 4 and to_call <= 6 * bb:
                return check_or_call(s)
            if score >= 8 and to_call <= 0.2 * s["my_stack"]:
                return check_or_call(s)
            return check_or_fold(s)
        eq = equity_vs_random(s["hole"], s["board"], 300)
        if to_call == 0:
            return raise_to(s, int(0.5 * pot)) if eq > 0.75 else "check"
        if eq > 0.92:
            return raise_to(s, s["current_bet"] * 2 + pot // 2)
        return "call" if eq >= pot_odds(to_call, pot) else "fold"


NIT_OPEN = {"AA", "KK", "QQ", "JJ", "TT", "99", "88", "77", "AKs", "AQs", "AJs", "ATs", "KQs", "AKo", "AQo", "AJo"}
NIT_CALL = {"TT", "99", "AQs", "AJs", "KQs", "AQo"}
NIT_3BET = {"AA", "KK", "QQ", "JJ", "AKs", "AKo"}
NIT_STACK = {"AA", "KK", "QQ", "AKs"}


def made_hand_class(hole, board):
    """'monster' (two pair+ using a hole card), 'strong' (overpair / top pair good kicker), 'weak'."""
    hc, bc = to_cards(hole), to_cards(board)
    v = eval7.evaluate(hc + bc)
    bv = eval7.evaluate(bc) if len(bc) >= 5 else -1
    kind = v >> 24
    if kind >= 2 and v != bv:
        return "monster"
    if kind == 1:
        top = max(RANK_VAL[c[0]] for c in board)
        r = sorted((RANK_VAL[c[0]] for c in hole), reverse=True)
        if r[0] == r[1] and r[0] > top:
            return "strong"
        if top in r:
            kicker = r[1] if r[0] == top else r[0]
            if kicker >= 10:
                return "strong"
            return "medium"
    return "weak"


class ChartNit(Bot):
    """Prompt: 'write a tight-aggressive bot with a preflop chart'. Plays ~5% of hands, bets its
    strong made hands, folds everything else to pressure."""
    name = "nit"

    def act(self, s):
        bb, to_call, pot = s["bb"], s["to_call"], s["pot"]
        cls = hand_class(s["hole"])
        if s["street"] == "preflop":
            raises = sum(1 for e in s["history"] if e.get("type") == "action" and e["action"] == "raise")
            if raises == 0:
                if cls in NIT_OPEN:
                    return raise_to(s, 3 * bb)
                return check_or_fold(s)
            if raises == 1:
                if cls in NIT_3BET:
                    return raise_to(s, 3 * s["current_bet"])
                if cls in NIT_CALL and to_call <= 6 * bb:
                    return "call"
                return check_or_fold(s)
            if cls in NIT_STACK:
                return raise_to(s, s["max_raise_to"] or 0) if "raise" in s["legal"] else "call"
            return check_or_fold(s)
        mh = made_hand_class(s["hole"], s["board"])
        if mh == "monster":
            return raise_to(s, s["current_bet"] + pot)
        if mh == "strong":
            if to_call == 0:
                return raise_to(s, pot // 2)
            return "call" if to_call <= 0.6 * (pot - to_call) else "fold"
        if mh == "medium" and to_call == 0:
            return "check"
        if mh == "medium" and to_call <= 0.35 * (pot - to_call) and s["street"] != "river":
            return "call"
        return check_or_fold(s)


class Maniac(Bot):
    """Prompt: 'write an aggressive poker bot that bluffs to win'. Raises and barrels constantly;
    folds when it runs out of story."""
    name = "maniac"

    def __init__(self, seed=None, **kw):
        super().__init__(**kw)
        self.rng = random.Random(seed)

    def act(self, s):
        bb, to_call, pot = s["bb"], s["to_call"], s["pot"]
        r = self.rng.random()
        if s["street"] == "preflop":
            score = chen(s["hole"])
            if s["current_bet"] <= bb:
                if score >= 6 or r < 0.25:
                    return raise_to(s, 4 * bb)
                return check_or_fold(s)
            if score >= 12 or (score >= 8 and r < 0.1):
                return raise_to(s, s["max_raise_to"] or 0)
            if score >= 9 or r < 0.15:
                return raise_to(s, 3 * s["current_bet"])
            if score >= 5 and to_call <= 0.15 * s["my_stack"]:
                return "call"
            return check_or_fold(s)
        eq = equity_vs_random(s["hole"], s["board"], 300)
        if to_call == 0:
            if eq > 0.6 or r < 0.65:
                return raise_to(s, int(pot * (0.75 + 0.25 * self.rng.random())))
            return "check"
        if eq > 0.75 or r < 0.2:
            return raise_to(s, s["current_bet"] + pot)
        if eq > 0.45 or r < 0.3:
            return "call"
        return "fold"


class GTOLite(Bot):
    """Prompt: 'write a GTO poker bot'. Position-based ranges, two bet sizes, 'balanced'
    bluff frequency, defends by minimum-defence frequency. Mixed strategies via random()."""
    name = "gto_lite"
    OPEN = {"UTG": 0.12, "UTG1": 0.13, "MP": 0.15, "HJ": 0.19, "CO": 0.26, "BTN": 0.42, "SB": 0.35, "BB": 0.0}

    def __init__(self, seed=None, **kw):
        super().__init__(**kw)
        self.rng = random.Random(seed)

    def act(self, s):
        bb, to_call, pot = s["bb"], s["to_call"], s["pot"]
        r = self.rng.random()
        if s["street"] == "preflop":
            pct = PCT_HU[hand_class(s["hole"])]
            raises = sum(1 for e in s["history"] if e.get("type") == "action" and e["action"] == "raise")
            if raises == 0:
                if pct <= self.OPEN.get(s["position"], 0.15):
                    return raise_to(s, int(2.5 * bb))
                return check_or_fold(s)
            if raises == 1:
                if pct <= 0.04 or (0.25 < pct <= 0.30 and r < 0.5):
                    return raise_to(s, 3 * s["current_bet"])
                if pct <= 0.15 and to_call <= 0.1 * s["my_stack"]:
                    return "call"
                return check_or_fold(s)
            if pct <= 0.02:
                return raise_to(s, s["max_raise_to"] or 0) if "raise" in s["legal"] else "call"
            if pct <= 0.05 and to_call <= 0.25 * s["my_stack"]:
                return "call"
            return check_or_fold(s)
        eq = equity_vs_random(s["hole"], s["board"], 300)
        if to_call == 0:
            if eq > 0.75:
                return raise_to(s, int(0.75 * pot)) if r < 0.7 else "check"
            if eq < 0.35 and r < 0.3:
                return raise_to(s, int(0.75 * pot))
            if eq > 0.55 and r < 0.5:
                return raise_to(s, int(0.33 * pot))
            return "check"
        mdf = (pot - to_call) / float(pot)
        if eq > 0.85 and r < 0.6:
            return raise_to(s, s["current_bet"] * 3)
        if eq >= 1 - mdf:
            return "call"
        if r < 0.04:
            return raise_to(s, s["current_bet"] * 3)
        return "fold"


class Sloppy(Bot):
    """ChenMC with the bugs that ship when there is no time to test: a rank map missing 'T'
    (crashes whenever it holds a ten postflop), 'raise by' confused with 'raise to', and a
    'check' returned while facing a bet. The dealer auto-folds each of these."""
    name = "sloppy"
    RANK = {"2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9, "J": 11, "Q": 12, "K": 13, "A": 14}

    def act(self, s):
        bb, to_call, pot = s["bb"], s["to_call"], s["pot"]
        if s["street"] == "preflop":
            score = chen(s["hole"])
            if score >= 10:
                return ("raise", 3 * bb if to_call <= bb else 2 * to_call)   # raise BY, not TO
            if score >= 7 and to_call <= 3 * bb:
                return "call"
            return "check" if to_call == 0 else "fold"
        high = max(self.RANK[c[0]] for c in s["hole"])           # KeyError on any ten
        eq = equity_vs_random(s["hole"], s["board"], 300)
        if eq > 0.8 or (eq > 0.7 and high >= 12):
            return ("raise", int(0.6 * pot))                        # raise BY again
        if eq < 0.4:
            return "check"                                          # illegal when facing a bet
        return "call"


class RandomBot(Bot):
    """Picks a random legal action. Useful as a smoke test, not a realistic opponent."""
    name = "random"

    def __init__(self, seed=None, **kw):
        super().__init__(**kw)
        self.rng = random.Random(seed)

    def act(self, s):
        a = self.rng.choice(s["legal"])
        if a == "raise":
            return ("raise", self.rng.randint(s["min_raise_to"], s["max_raise_to"]))
        return a
