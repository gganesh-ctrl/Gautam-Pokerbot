"""
Where does a bot win and lose? Break a hand log down by position, by how far the hand went,
heads-up vs multiway, and showdown vs no showdown.

  python run.py --hands 3000 --log hands.jsonl
  python tools/analyze_log.py hands.jsonl exploit
  python tools/analyze_log.py hands.jsonl exploit --vs station      # only hands that included station
  python tools/analyze_log.py hands.jsonl exploit --worst 10        # print the 10 biggest losses
"""
import argparse
import json
from collections import defaultdict


def street_reached(history, name):
    last = "preflop"
    folded_on = None
    for e in history:
        if e.get("type") == "street":
            if folded_on is None:
                last = e["street"]
        if e.get("type") == "action" and e["player"] == name and e["action"] == "fold" and folded_on is None:
            folded_on = e["street"]
    return folded_on, last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("pid")
    ap.add_argument("--vs", default=None)
    ap.add_argument("--worst", type=int, default=0)
    ap.add_argument("--bb", type=int, default=100)
    a = ap.parse_args()

    groups = defaultdict(lambda: [0, 0])
    hands = []
    with open(a.log) as f:
        for line in f:
            h = json.loads(line)
            seat = next((s for s in h["seats"] if s["pid"] == a.pid), None)
            if seat is None:
                continue
            if a.vs and not any(s["pid"].startswith(a.vs) for s in h["seats"]):
                continue
            name, net = seat["name"], h["net"][a.pid]
            hands.append((net, h, seat))
            folded_on, last = street_reached(h["history"], name)
            vol = any(e.get("type") == "action" and e["player"] == name and e["action"] in ("call", "raise")
                      for e in h["history"])
            showdown = any(e.get("type") == "showdown" and name in e["shown"] for e in h["history"])
            flop_players = None
            for i, e in enumerate(h["history"]):
                if e.get("type") == "street" and e["street"] == "flop":
                    folded = {x["player"] for x in h["history"][:i] if x.get("type") == "action" and x["action"] == "fold"}
                    flop_players = len(h["seats"]) - len(folded)
                    break

            def add(k):
                groups[k][0] += net
                groups[k][1] += 1
            add("ALL")
            add("pos " + seat["position"])
            if not vol:
                add("preflop: never put chips in voluntarily")
            elif folded_on == "preflop":
                add("preflop: played then folded preflop")
            elif flop_players is None:
                add("preflop: won/lost before the flop")
            else:
                add("saw flop: %s" % ("heads-up" if flop_players == 2 else "%d-way" % min(flop_players, 4)))
                add("saw flop, ended: %s" % ("showdown" if showdown else ("we folded on " + folded_on if folded_on else "they folded")))

    print("%-44s %8s %10s %10s" % ("bucket", "hands", "net bb", "bb/100*"))
    total = groups["ALL"][1] or 1
    for k in sorted(groups, key=lambda k: (not k.startswith("ALL"), k)):
        net, n = groups[k]
        print("%-44s %8d %+10.1f %+10.1f" % (k, n, net / a.bb, net / a.bb / total * 100))
    print("* contribution to the overall bb/100")

    if a.worst:
        hands.sort(key=lambda x: x[0])
        for net, h, seat in hands[: a.worst]:
            print("\nhand %s  net %+d  %s %s  board %s" % (h["hand_id"], net, seat["position"], " ".join(seat["hole"]), " ".join(h["board"])))
            holes = {s["name"]: (s["pid"], " ".join(s["hole"])) for s in h["seats"]}
            for e in h["history"]:
                if e.get("type") == "street":
                    print("   -- %s %s" % (e["street"], " ".join(e["board"])))
                elif e.get("type") == "action" and e["action"] != "fold":
                    pid, hole = holes[e["player"]]
                    amt = (" %d" % e["amount"]) if e["action"] in ("raise", "call", "blind") else ""
                    print("   %-14s %-6s %-6s%s" % (pid[:14], e["action"], hole, amt))


if __name__ == "__main__":
    main()
