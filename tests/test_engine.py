"""Engine correctness checks. Run: python -m pytest tests -q   (or: python tests/test_engine.py)"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from engine import FULL_DECK, Config, normalize_action, play_hand, position_labels  # noqa: E402
from bots.field import RandomBot  # noqa: E402


class Script:
    """Returns scripted actions in order; after the script runs out, checks or folds."""
    def __init__(self, actions=()):
        self.actions = list(actions)
        self.seen = []
        self.states = []

    def observe(self, ev):
        self.seen.append(ev)

    def act(self, s):
        self.states.append(s)
        if self.actions:
            return self.actions.pop(0)
        return "check" if s["to_call"] == 0 else "fold"


def deck_with(holes, board):
    """Deck that deals `holes` in dealing order (seat button+1 first) then `board`."""
    used = [c for h in holes for c in h] + board
    rest = [c for c in FULL_DECK if c not in used]
    return [c for h in holes for c in h] + board + rest


def test_positions_8max():
    lab = position_labels(8, 0)
    assert [lab[i] for i in range(8)] == ["BTN", "SB", "BB", "UTG", "UTG1", "MP", "HJ", "CO"]
    assert position_labels(2, 1) == {1: "BTN", 0: "BB"}


def test_normalize():
    assert normalize_action(("raise", 300)) == ("raise", 300)
    assert normalize_action("bet 250") == ("raise", 250)
    assert normalize_action("ALL-IN") == ("allin", 0)
    assert normalize_action({"action": "call"}) == ("call", 0)
    assert normalize_action("dance")[0] == "invalid"
    assert normalize_action(None)[0] == "invalid"


def test_random_bots_conserve_chips():
    rng = random.Random(3)
    cfg = Config()
    bots = [("r%d" % i, RandomBot(seed=i)) for i in range(8)]
    for h in range(400):
        deck = FULL_DECK[:]
        rng.shuffle(deck)
        r = play_hand(bots, h % 8, deck, cfg, h)      # play_hand asserts conservation itself
        assert sum(r.net.values()) == 0
        assert not [v for v in r.violations if v[1] != "observe_error"]


def test_blinds_and_preflop_order():
    a, b, c = Script(), Script(), Script()
    deck = FULL_DECK[:]
    play_hand([("a", a), ("b", b), ("c", c)], 0, deck, Config(), 1)
    # 3-handed, button seat 0: SB seat 1, BB seat 2, first to act preflop = button
    assert a.states[0]["to_call"] == 100 and a.states[0]["position"] == "BTN"


def test_min_raise_enforced_and_illegal_folds():
    utg = Script([("raise", 150)])          # min raise is to 200
    others = [Script() for _ in range(3)]
    seats = [("btn", others[0]), ("sb", others[1]), ("bb", others[2]), ("utg", utg)]
    r = play_hand(seats, 0, FULL_DECK[:], Config(), 1)
    assert ("utg", "illegal") in [(v[0], v[1]) for v in r.violations]
    assert r.net["utg"] == 0                 # folded, put nothing in
    assert r.net["bb"] == 50                 # everyone folded to BB, wins SB


def test_clamp_mode_fixes_small_raise():
    utg = Script([("raise", 150)])
    seats = [("btn", Script()), ("sb", Script()), ("bb", Script()), ("utg", utg)]
    r = play_hand(seats, 0, FULL_DECK[:], Config(illegal="clamp"), 1)
    assert r.net["utg"] == 150               # raised to 200, everyone folded: wins blinds


def test_check_facing_bet_is_fold():
    utg = Script(["check"])
    seats = [("btn", Script()), ("sb", Script()), ("bb", Script()), ("utg", utg)]
    r = play_hand(seats, 0, FULL_DECK[:], Config(), 1)
    assert r.net["utg"] == 0 and any(v[1] == "illegal" for v in r.violations)


def test_exception_and_timeout():
    class Boom:
        def observe(self, ev): pass
        def act(self, s): raise ValueError("bug")

    class Slow:
        def observe(self, ev): pass
        def act(self, s):
            import time; time.sleep(0.05); return "call"

    seats = [("btn", Script()), ("sb", Script()), ("bb", Script()), ("boom", Boom())]
    r = play_hand(seats, 0, FULL_DECK[:], Config(), 1)
    assert any(v[1] == "exception" for v in r.violations)
    seats = [("btn", Script()), ("sb", Script()), ("bb", Script()), ("slow", Slow())]
    r = play_hand(seats, 0, FULL_DECK[:], Config(time_limit=0.01), 1)
    assert any(v[1] == "timeout" for v in r.violations)


def test_side_pots():
    # seats: 0 btn (short, 1000), 1 sb (3000), 2 bb (10000). All go all-in preflop.
    # dealing order: seat1, seat2, seat0. Best hand: seat0 (AA) > seat1 (KK) > seat2 (QQ)
    holes = [["Kh", "Kd"], ["Qh", "Qd"], ["Ah", "Ad"]]
    board = ["2c", "7s", "9c", "3d", "4s"]
    deck = deck_with(holes, board)
    s0, s1, s2 = Script(["allin"]), Script(["allin"]), Script(["call"])
    r = play_hand([("short", s0), ("mid", s1), ("big", s2)], 0, deck, Config(), 1,
                  stacks=[1000, 3000, 10000])
    # main pot 3000 -> short; side pot 4000 (2000 each from mid & big) -> mid; big loses 3000
    assert r.net == {"short": 2000, "mid": 1000, "big": -3000}, r.net


def test_split_pot_odd_chip():
    holes = [["Ah", "2d"], ["As", "2c"], ["7h", "8h"]]   # seat1, seat2, seat0
    board = ["Kc", "Kd", "Qs", "Qh", "Jc"]               # board plays for both aces? no: A kicker plays
    deck = deck_with(holes, board)
    s0, s1, s2 = Script(["fold"]), Script(["call"]), Script(["check", "check", "check", "check"])
    r = play_hand([("btn", s0), ("sb", s1), ("bb", s2)], 0, deck, Config(sb=50, bb=100), 1)
    assert r.net["sb"] == 0 and r.net["bb"] == 0          # same hand, pot split evenly


def test_short_allin_does_not_reopen():
    # A raises to 400, B shoves 500 (not a full raise), A may only call or fold.
    A = Script([("raise", 400), ("raise", 1500)])
    B = Script(["allin"])
    C = Script(["fold"])
    D = Script(["fold"])
    seats = [("A", A), ("B", B), ("C", C), ("D", D)]
    # button 0 -> SB seat1 (B), BB seat2 (C), UTG seat3 (D)... make A act after B's shove:
    r = play_hand(seats, 3, FULL_DECK[:], Config(), 1, stacks=[10000, 500, 10000, 10000])
    # button 3 -> SB 0 (A), BB 1 (B), first to act seat 2 (C)
    last = A.states[-1]
    assert "raise" not in last["legal"], last["legal"]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for f in fns:
        f()
        print("ok  ", f.__name__)
    print("all %d engine tests passed" % len(fns))
