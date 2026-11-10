"""Precompute equity of all 169 starting hands vs 1..7 random opponents -> data/preflop_equity.json.
Takes about a minute. Re-run only if you want more precision (raise ITERS)."""
import json, os, random, sys
from multiprocessing import Pool
import eval7

RANKS = "23456789TJQKA"
ITERS = int(os.environ.get("ITERS", 6000))
ALL = [eval7.Card(r + s) for r in RANKS for s in "cdhs"]
RANDOM_RANGE = eval7.HandRange("22+,A2s+,K2s+,Q2s+,J2s+,T2s+,92s+,82s+,72s+,62s+,52s+,42s+,32s,"
                               "A2o+,K2o+,Q2o+,J2o+,T2o+,92o+,82o+,72o+,62o+,52o+,42o+,32o")


def classes():
    out = []
    for i, hi in enumerate(reversed(RANKS)):
        for lo in reversed(RANKS[: len(RANKS) - i]):
            if hi == lo:
                out.append(hi + lo)
            else:
                out.append(hi + lo + "s")
                out.append(hi + lo + "o")
    return out


def rep(cls):
    if len(cls) == 2:
        return [eval7.Card(cls[0] + "s"), eval7.Card(cls[1] + "h")]
    if cls[2] == "s":
        return [eval7.Card(cls[0] + "s"), eval7.Card(cls[1] + "s")]
    return [eval7.Card(cls[0] + "s"), eval7.Card(cls[1] + "h")]


def equity(args):
    cls, seed = args
    rng = random.Random(seed)
    hole = rep(cls)
    out = [eval7.py_hand_vs_range_monte_carlo(hole, RANDOM_RANGE, [], 30000)]
    rest = [c for c in ALL if c not in hole]
    for k in range(2, 8):
        score = 0.0
        for _ in range(ITERS):
            s = rng.sample(rest, 5 + 2 * k)
            b = s[:5]
            me = eval7.evaluate(hole + b)
            best, ties = 0, 0
            for j in range(k):
                v = eval7.evaluate(s[5 + 2 * j: 7 + 2 * j] + b)
                if v > best:
                    best, ties = v, 1
                elif v == best:
                    ties += 1
            if me > best:
                score += 1
            elif me == best:
                score += 1 / (ties + 1)
        out.append(score / ITERS)
    return cls, [round(x, 4) for x in out]


if __name__ == "__main__":
    cl = classes()
    assert len(cl) == 169
    with Pool() as pool:
        res = dict(pool.map(equity, [(c, i) for i, c in enumerate(cl)]))
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "data", "preflop_equity.json")
    with open(path, "w") as f:
        json.dump(res, f, indent=0)
    top = sorted(res, key=lambda c: -res[c][0])[:10]
    print("wrote", path, "top vs 1:", top)
