"""Measure the exploit bot's per-decision latency against the field under the event's time rule.
  python tools/latency.py [hands] [time_limit_seconds]"""
import os
import random
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import eval7.xorshift_rand as xr  # noqa: E402
from engine import FULL_DECK, Config, play_hand  # noqa: E402
from bots import DEFAULT_FIELD, REGISTRY  # noqa: E402

hands = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
limit = float(sys.argv[2]) if len(sys.argv) > 2 else 0.001
times = {"preflop": [], "postflop heads-up": [], "postflop multiway": []}
bot = REGISTRY["exploit"](seed=1)
inner = bot.act


def timed(s):
    t = time.perf_counter()
    a = inner(s)
    dt = time.perf_counter() - t
    if s["street"] == "preflop":
        times["preflop"].append(dt)
    else:
        live = sum(1 for p in s["players"] if not p["folded"])
        times["postflop heads-up" if live == 2 else "postflop multiway"].append(dt)
    return a


bot.act = timed
bots = [("exploit", bot)] + [(n, REGISTRY[n](seed=i)) for i, n in enumerate(DEFAULT_FIELD)]
rng = random.Random(2)
xr.seed(2)
random.seed(2)
violations = {}
for h in range(hands):
    deck = FULL_DECK[:]
    rng.shuffle(deck)
    r = play_hand(bots, h % 8, deck, Config(time_limit=limit), h)
    for pid, kind, _ in r.violations:
        violations[(pid, kind)] = violations.get((pid, kind), 0) + 1
print("time limit %.3f ms, %d hands" % (limit * 1e3, hands))
for k, v in times.items():
    v.sort()
    if v:
        q = lambda p: v[min(len(v) - 1, int(len(v) * p))] * 1e3
        print("%-18s %6d decisions  median %.3f ms  p99 %.3f  p99.9 %.3f  max %.3f"
              % (k, len(v), q(0.5), q(0.99), q(0.999), v[-1] * 1e3))
print("exploit: ran out of budget and used the fallback %d times, internal errors %d" % (bot.time_ups, bot.errors))
for (pid, kind), c in sorted(violations.items()):
    print("  auto-fold  %-10s %-10s %d" % (pid, kind, c))
