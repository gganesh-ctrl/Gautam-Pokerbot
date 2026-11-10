#!/usr/bin/env python3
"""
Run the lab.

  python run.py                                   exploit vs the 7-bot field, 2,000 hands
  python run.py --hands 10000 --duplicate         full-length, card-luck-balanced run
  python run.py --param use_tracker=0             ablation: switch a feature off
  python run.py --sweep open_size_bb=2.5,3,3.5    tune one parameter
  python run.py --lineup exploit,chen_mc,station,nit,maniac,gto_lite,sloppy,mc_multi
  python run.py --lineup "exploit,exploit?use_tracker=0,..."   A/B two versions at one table
  python run.py --lineup exploit,bots/external/gpt_bot.py,...  add AI-generated bots
  python run.py --anonymous                       opponents get a fresh random name every hand
  python run.py --field 16                        16 bots, a random 8 seated each hand
  python run.py --report                          print what the exploit bot learned
  python run.py --log hands.jsonl --hands 200     dump every hand for debugging

--duplicate deals each deck 8 times, rotating seats, so every bot plays every seat with
the same cards. It removes most card luck, so you can trust smaller differences.
"""
import argparse
import importlib.util
import inspect
import json
import math
import os
import random
import sys
import time
from collections import defaultdict
from multiprocessing import Pool

import eval7
import eval7.xorshift_rand

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine import FULL_DECK, Config, play_hand        # noqa: E402
from bots import DEFAULT_FIELD, REGISTRY                # noqa: E402
from bots.base import Bot                               # noqa: E402


# ------------------------------------------------------------------ building bots
def load_class(spec):
    """'chen_mc' | 'path/to/file.py' | 'path/to/file.py:ClassName'."""
    if spec in REGISTRY:
        return REGISTRY[spec], spec
    path, _, cls_name = spec.partition(":")
    if not path.endswith(".py") or not os.path.exists(path):
        raise SystemExit("unknown bot %r (registry: %s, or a .py file path)" % (spec, ", ".join(REGISTRY)))
    mod_name = "ext_" + os.path.splitext(os.path.basename(path))[0]
    spec_obj = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec_obj)
    spec_obj.loader.exec_module(mod)
    if cls_name:
        return getattr(mod, cls_name), os.path.splitext(os.path.basename(path))[0]
    for _, obj in inspect.getmembers(mod, inspect.isclass):
        if obj.__module__ == mod_name and hasattr(obj, "act"):
            return obj, os.path.splitext(os.path.basename(path))[0]
    raise SystemExit("no class with act() found in %s" % path)


def make_bot(entry, seed, global_params):
    spec, _, qs = entry.partition("?")
    cls, base_name = load_class(spec)
    params = {}
    if base_name == "exploit":
        params.update(global_params)
    for kv in filter(None, qs.split("&")):
        k, _, v = kv.partition("=")
        params[k] = v
    kwargs = dict(params)
    sig = inspect.signature(cls.__init__)
    accepts_kw = any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values())
    if "seed" in sig.parameters or accepts_kw:
        kwargs["seed"] = seed
    try:
        bot = cls(**kwargs)
    except TypeError:
        bot = cls()
    label = base_name + ("?" + qs if qs else "")
    return bot, label


def build_lineup(entries, seed, global_params):
    bots, seen = [], defaultdict(int)
    for i, e in enumerate(entries):
        bot, label = make_bot(e, seed * 1000 + i, global_params)
        seen[label] += 1
        pid = label if seen[label] == 1 else "%s#%d" % (label, seen[label])
        bots.append((pid, bot))
    return bots


# ------------------------------------------------------------------ simulation
def simulate(job):
    entries, hands, seed, opts = job["entries"], job["hands"], job["seed"], job["opts"]
    cfg = Config(time_limit=opts["time_limit"], illegal=opts["illegal"])
    rng = random.Random(seed)
    random.seed(seed)                       # bots that use the global RNG
    eval7.xorshift_rand.seed(seed)          # eval7's C-level Monte Carlo

    lineup = build_lineup(entries, seed, opts["params"])
    table = opts["table"]
    n = min(table, len(lineup))
    blocks = defaultdict(list)          # pid -> list of per-block nets (block = 1 hand or 1 deck x rotations)
    played = defaultdict(int)
    violations = defaultdict(lambda: defaultdict(int))
    examples = defaultdict(list)
    think = defaultdict(float)
    log = [] if opts.get("log") else None
    hand_id = 0

    def run_one(seat_bots, button, deck):
        nonlocal hand_id
        hand_id += 1
        alias = None
        if opts["anonymous"]:
            alias = {pid: "p%06d" % rng.randrange(10 ** 6) for pid, _ in seat_bots}
        r = play_hand(seat_bots, button, deck, cfg, hand_id, alias, log)
        for pid, kind, detail in r.violations:
            violations[pid][kind] += 1
            if len(examples[pid]) < 3:
                examples[pid].append("%s: %s" % (kind, detail))
        for pid, t in r.max_think.items():
            think[pid] = max(think[pid], t)
        return r

    if opts["duplicate"]:
        decks = max(1, hands // n)
        for d in range(decks):
            deck = FULL_DECK[:]
            rng.shuffle(deck)
            seated = rng.sample(lineup, n) if len(lineup) > n else list(lineup)
            block = defaultdict(int)
            for rot in range(n):
                seat_bots = seated[rot:] + seated[:rot]
                r = run_one(seat_bots, 0, deck)
                for pid, v in r.net.items():
                    block[pid] += v
                    played[pid] += 1
            for pid, v in block.items():
                blocks[pid].append(v / float(n))
    else:
        seated = list(lineup)
        rng.shuffle(seated)
        for h in range(hands):
            deck = FULL_DECK[:]
            rng.shuffle(deck)
            if len(lineup) > n:
                seat_bots = rng.sample(lineup, n)
                button = 0
            else:
                seat_bots, button = seated, h % n
            r = run_one(seat_bots, button, deck)
            for pid, v in r.net.items():
                blocks[pid].append(v)
                played[pid] += 1

    report, fitted = None, None
    for pid, bot in lineup:
        if opts["report"] and hasattr(bot, "report") and report is None:
            report = (pid, bot.report())
        if hasattr(bot, "fitted_priors") and fitted is None:
            fitted = bot.fitted_priors()
    errors = {pid: getattr(bot, "errors", 0) for pid, bot in lineup}
    if log is not None:
        with open(opts["log"], "w") as f:
            for rec in log:
                f.write(json.dumps(rec) + "\n")
    return {"blocks": dict(blocks), "played": dict(played), "think": dict(think),
            "violations": {k: dict(v) for k, v in violations.items()}, "examples": dict(examples),
            "report": report, "errors": errors, "fitted": fitted}


def merge(results):
    out = {"blocks": defaultdict(list), "played": defaultdict(int), "think": defaultdict(float),
           "violations": defaultdict(lambda: defaultdict(int)), "examples": defaultdict(list),
           "report": None, "errors": defaultdict(int), "fitted": None}
    for r in results:
        for pid, b in r["blocks"].items():
            out["blocks"][pid].extend(b)
        for pid, c in r["played"].items():
            out["played"][pid] += c
        for pid, t in r["think"].items():
            out["think"][pid] = max(out["think"][pid], t)
        for pid, v in r["violations"].items():
            for k, c in v.items():
                out["violations"][pid][k] += c
        for pid, ex in r["examples"].items():
            out["examples"][pid].extend(ex[: 3 - len(out["examples"][pid])])
        for pid, e in r["errors"].items():
            out["errors"][pid] += e
        out["report"] = out["report"] or r["report"]
        out["fitted"] = out["fitted"] or r.get("fitted")
    return out


def stats(blocks, bb):
    """Mean bb/100 and a 95% interval from per-block nets."""
    k = len(blocks)
    if k == 0:
        return 0.0, 0.0
    mean = sum(blocks) / k
    var = sum((x - mean) ** 2 for x in blocks) / max(1, k - 1)
    se = math.sqrt(var / k)
    return mean / bb * 100, 1.96 * se / bb * 100


def run(entries, hands, seed, opts, workers):
    per = max(1, hands // workers)
    jobs = [{"entries": entries, "hands": per, "seed": seed + 7919 * w, "opts": opts} for w in range(workers)]
    if workers == 1:
        return merge([simulate(jobs[0])])
    with Pool(workers) as pool:
        return merge(pool.map(simulate, jobs))


def print_table(res, bb=100):
    rows = []
    for pid, b in res["blocks"].items():
        m, ci = stats(b, bb)
        v = res["violations"].get(pid, {})
        rows.append((m, ci, pid, res["played"][pid], v, res["think"].get(pid, 0)))
    rows.sort(key=lambda r: -r[0])
    print("\n%-26s %9s %8s %8s  %-30s %8s" % ("bot", "bb/100", "±95%", "hands", "auto-folds (illegal/crash/timeout)", "max ms"))
    print("-" * 98)
    for m, ci, pid, h, v, t in rows:
        vs = "%d / %d / %d" % (v.get("illegal", 0), v.get("exception", 0), v.get("timeout", 0))
        print("%-26s %+9.1f %8.1f %8d  %-34s %6.0f" % (pid[:26], m, ci, h, vs, t * 1000))
    bad = {pid: ex for pid, ex in res["examples"].items() if ex}
    if bad:
        print("\nexample violations:")
        for pid, ex in bad.items():
            for e in ex:
                print("  %-22s %s" % (pid[:22], e))
    errs = {pid: e for pid, e in res["errors"].items() if e}
    if errs:
        print("\ninternal errors caught by fallbacks:", errs)


def print_report(report):
    if not report:
        return
    pid, (cols, rows) = report
    print("\nWhat %s learned about each opponent (smoothed):" % pid)
    print("%-16s " % "player" + " ".join("%9s" % c[:9] for c in cols[:10]) + "  %-20s %s" % (cols[10], cols[11]))
    for r in rows:
        nums = " ".join(("%9d" % x) if isinstance(x, int) else ("%9.2f" % x) for x in r[1:11])
        print("%-16s %s  %-20s %s" % (str(r[0])[:16], nums, r[11], r[12]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hands", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--lineup", default=None, help="comma-separated bot names or .py paths")
    ap.add_argument("--field", type=int, default=0, help="field size; repeats the default archetypes")
    ap.add_argument("--table", type=int, default=8)
    ap.add_argument("--duplicate", action="store_true")
    ap.add_argument("--anonymous", action="store_true")
    ap.add_argument("--param", action="append", default=[], help="exploit param override k=v")
    ap.add_argument("--sweep", default=None, help="k=v1,v2,v3 : run once per value")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--time-limit", type=float, default=0.001, help="seconds per action (event rule: 1 ms)")
    ap.add_argument("--illegal", choices=["fold", "clamp"], default="fold")
    ap.add_argument("--realtime", action="store_true",
                    help="keep the exploit bot's real time budget (runs are then not exactly repeatable)")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--log", default=None)
    ap.add_argument("--fit-priors", action="store_true",
                    help="print the field's average tendencies as a PRIORS_OVERRIDE dict for bots/exploit.py")
    a = ap.parse_args()

    if a.lineup:
        entries = [e.strip() for e in a.lineup.split(",") if e.strip()]
    else:
        entries = ["exploit"] + DEFAULT_FIELD
        if a.field > len(entries):
            entries += [DEFAULT_FIELD[i % len(DEFAULT_FIELD)] for i in range(a.field - len(entries))]
    params = dict(kv.split("=", 1) for kv in a.param)
    if not a.realtime and "time_budget" not in params:
        params["time_budget"] = "5"   # no wall-clock cutoffs, so identical seeds give identical runs
    opts = {"time_limit": a.time_limit, "illegal": a.illegal, "duplicate": a.duplicate,
            "anonymous": a.anonymous, "table": a.table, "params": params,
            "report": a.report and a.workers == 1, "log": a.log}

    t0 = time.time()
    if a.sweep:
        key, _, vals = a.sweep.partition("=")
        print("sweep %s over %s | %d hands each%s" % (key, vals, a.hands, " | duplicate" if a.duplicate else ""))
        print("%-12s %9s %8s" % (key[:12], "bb/100", "±95%"))
        for v in vals.split(","):
            o = dict(opts, params=dict(params, **{key: v}), report=False)
            res = run(entries, a.hands, a.seed, o, a.workers)
            me = [pid for pid in res["blocks"] if pid.startswith("exploit")]
            for pid in me:
                m, ci = stats(res["blocks"][pid], 100)
                print("%-12s %+9.1f %8.1f   %s" % (v, m, ci, pid if len(me) > 1 else ""))
    else:
        res = run(entries, a.hands, a.seed, opts, a.workers)
        print("%d hands | %d bots | seed %d%s%s%s" % (sum(res["played"].values()) // min(a.table, len(entries)),
              len(entries), a.seed, " | duplicate" if a.duplicate else "",
              " | anonymous" if a.anonymous else "", " | illegal=clamp" if a.illegal == "clamp" else ""))
        print_table(res)
        print_report(res["report"])
        if a.fit_priors and res["fitted"]:
            out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "fitted_priors.json")
            with open(out, "w") as f:
                json.dump(res["fitted"], f, indent=1)
            print("\nsaved %s  (use with --param priors=data/fitted_priors.json)" % os.path.relpath(out))
            print("PRIORS_OVERRIDE = {")
            items = list(res["fitted"].items())
            for i in range(0, len(items), 4):
                print("    " + " ".join('"%s": %s,' % kv for kv in items[i:i + 4]))
            print("}")
    print("\n(%.0fs)" % (time.time() - t0))


if __name__ == "__main__":
    main()
