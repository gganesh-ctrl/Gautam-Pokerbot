"""
Runs one battle in its own process and streams JSON lines to stdout. The arena server
starts it, reads the lines, and kills it if you press Stop. Keeping battles out of the
server process means a friend's bot can crash or hang without taking the arena down.

    python arena/worker.py '<json config>'
"""
import json
import math
import os
import random
import signal
import sys
import time
import traceback
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import eval7.xorshift_rand  # noqa: E402

from engine import FULL_DECK, Config, play_hand  # noqa: E402
from run import build_lineup  # noqa: E402

HARD_KILL = 0.25          # a single act() that runs this long is interrupted outright


def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


class HardTimeout(Exception):
    pass


def _alarm(signum, frame):
    raise HardTimeout("bot ran longer than %d ms and was interrupted" % (HARD_KILL * 1000))


class Guarded:
    """Wraps a bot so an infinite loop can't freeze the battle (Unix/macOS only)."""

    def __init__(self, bot):
        self._bot = bot

    def __getattr__(self, name):
        return getattr(self._bot, name)

    def observe(self, ev):
        f = getattr(self._bot, "observe", None)
        if f is None:
            return
        signal.setitimer(signal.ITIMER_REAL, HARD_KILL * 4)
        try:
            f(ev)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)

    def act(self, s):
        signal.setitimer(signal.ITIMER_REAL, HARD_KILL)
        try:
            return self._bot.act(s)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)


def ci95(values):
    k = len(values)
    if k < 2:
        return 0.0
    m = sum(values) / k
    var = sum((x - m) ** 2 for x in values) / (k - 1)
    return 1.96 * math.sqrt(var / k)


def main():
    cfg_in = json.loads(sys.argv[1])
    seats = cfg_in["seats"]
    hands = int(cfg_in.get("hands", 2000))
    duplicate = bool(cfg_in.get("duplicate", False))
    seed = int(cfg_in.get("seed", 1))
    bb = 100
    cfg = Config(time_limit=float(cfg_in.get("time_limit", 0.001)), illegal=cfg_in.get("illegal", "fold"))

    random.seed(seed)
    eval7.xorshift_rand.seed(seed)
    rng = random.Random(seed)
    try:
        lineup = build_lineup(seats, seed, {})
    except BaseException as e:     # load_class raises SystemExit for unknown bots
        emit({"type": "error", "message": "Couldn't load a bot: %s" % e,
              "detail": traceback.format_exc()[-1500:]})
        return
    if hasattr(signal, "setitimer"):
        signal.signal(signal.SIGALRM, _alarm)
        lineup = [(pid, Guarded(bot)) for pid, bot in lineup]

    pids = [pid for pid, _ in lineup]
    n = len(lineup)
    emit({"type": "start", "pids": pids, "hands": hands})

    blocks = defaultdict(list)                 # pid -> per-block nets in chips
    total = defaultdict(int)
    played = defaultdict(int)
    violations = defaultdict(lambda: defaultdict(int))
    examples = defaultdict(list)
    slowest = defaultdict(float)
    series = [{"hand": 0, "bb": {p: 0.0 for p in pids}}]
    featured = []                              # biggest pots, kept for replay
    every = max(1, hands // 120)
    t0 = time.time()
    last_emit = 0.0
    hand_id = 0

    def record(r, log):
        for pid, kind, detail in r.violations:
            violations[pid][kind] += 1
            if len(examples[pid]) < 3:
                examples[pid].append("%s: %s" % (kind, detail))
        for pid, t in r.max_think.items():
            slowest[pid] = max(slowest[pid], t)
        for pid, v in r.net.items():
            total[pid] += v
            played[pid] += 1
        rec = log[0]
        rec["pot"] = r.pot
        if len(featured) < 8 or r.pot > featured[-1]["pot"]:
            featured.append(rec)
            featured.sort(key=lambda h: -h["pot"])
            del featured[8:]

    def standings():
        out = []
        for pid in pids:
            b = blocks[pid]
            mean = (sum(b) / len(b)) if b else 0.0
            out.append({"pid": pid, "bb100": mean / bb * 100, "ci": ci95(b) / bb * 100,
                        "net_bb": total[pid] / float(bb), "hands": played[pid],
                        "illegal": violations[pid].get("illegal", 0),
                        "crash": violations[pid].get("exception", 0),
                        "timeout": violations[pid].get("timeout", 0),
                        "slowest_ms": slowest[pid] * 1000, "examples": examples[pid]})
        out.sort(key=lambda r: -r["bb100"])
        return out

    def progress(done, force=False):
        nonlocal last_emit
        series.append({"hand": done, "bb": {p: total[p] / float(bb) for p in pids}})
        now = time.time()
        if force or now - last_emit > 0.4:
            last_emit = now
            emit({"type": "progress", "done": done, "hands": hands, "elapsed": now - t0,
                  "standings": standings(), "series": series})

    try:
        if duplicate:
            decks = max(1, hands // n)
            done = 0
            for d in range(decks):
                deck = FULL_DECK[:]
                rng.shuffle(deck)
                block = defaultdict(int)
                for rot in range(n):
                    seat_bots = lineup[rot:] + lineup[:rot]
                    hand_id += 1
                    log = []
                    r = play_hand(seat_bots, 0, deck, cfg, hand_id, log=log)
                    record(r, log)
                    for pid, v in r.net.items():
                        block[pid] += v
                    done += 1
                    if done % every == 0:
                        progress(done)
                for pid, v in block.items():
                    blocks[pid].append(v / float(n))
        else:
            for h in range(hands):
                deck = FULL_DECK[:]
                rng.shuffle(deck)
                hand_id += 1
                log = []
                r = play_hand(lineup, h % n, deck, cfg, hand_id, log=log)
                record(r, log)
                for pid, v in r.net.items():
                    blocks[pid].append(v)
                if (h + 1) % every == 0:
                    progress(h + 1)
    except Exception as e:
        emit({"type": "error", "message": "The battle stopped: %s" % e, "detail": traceback.format_exc()[-1500:]})
        return

    done = sum(played.values()) // n
    series.append({"hand": done, "bb": {p: total[p] / float(bb) for p in pids}})
    # thin the chart series to ~200 points
    step = max(1, len(series) // 200)
    thin = series[::step]
    if thin[-1] is not series[-1]:
        thin.append(series[-1])
    emit({"type": "done", "done": done, "hands": hands, "elapsed": time.time() - t0,
          "standings": standings(), "series": thin, "featured": featured})


if __name__ == "__main__":
    main()
