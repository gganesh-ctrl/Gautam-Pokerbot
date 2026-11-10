"""Your bot must never be auto-folded. Run: python tests/test_bots.py"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from engine import FULL_DECK, Config, play_hand  # noqa: E402
from bots import REGISTRY  # noqa: E402


def check_clean(lineup_names, hands=300, seed=5):
    rng = random.Random(seed)
    bots = [("%s#%d" % (n, i), REGISTRY[n](seed=i)) for i, n in enumerate(lineup_names)]
    me = bots[0][0]
    for h in range(hands):
        deck = FULL_DECK[:]
        rng.shuffle(deck)
        r = play_hand(bots, h % len(bots), deck, Config(time_limit=0.5), h)
        bad = [v for v in r.violations if v[0] == me]
        assert not bad, bad
    assert bots[0][1].errors == 0, "exploit bot hit %d internal errors" % bots[0][1].errors


def test_exploit_clean_vs_random_bots():
    check_clean(["exploit"] + ["random"] * 7)


def test_exploit_clean_vs_field():
    check_clean(["exploit", "chen_mc", "mc_multi", "station", "nit", "maniac", "gto_lite", "sloppy"])


def test_exploit_clean_heads_up_and_short_tables():
    check_clean(["exploit", "maniac"], hands=200)
    check_clean(["exploit", "random", "station"], hands=200)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok  ", name)
    print("bot tests passed")
