"""
8-handed No-Limit Hold'em engine that mirrors the tournament format as published:
blinds 50/100, every stack reset to 10,000 at the start of every hand, "raise to"
semantics, a dealer that asks each bot for an action and auto-folds bad replies.

Bots implement two methods (see bots/base.py):
    observe(event: dict)  -> None      public events: hand_start, action, street, showdown, hand_end
    act(state: dict)      -> action    'fold' | 'check' | 'call' | ('raise', raise_to) | 'allin'

Strict by default: an illegal action, exception, or reply slower than the time limit is
replaced by check (if free) or fold, and is counted against the bot. Run with
illegal='clamp' to see how a forgiving dealer would treat the same bot.
"""
import time
import eval7

RANKS = "23456789TJQKA"
SUITS = "cdhs"
FULL_DECK = [r + s for r in RANKS for s in SUITS]
STREETS = ["preflop", "flop", "turn", "river"]
MIDDLE_LABELS = ["UTG", "UTG1", "MP", "HJ", "CO"]

_CARD = {c: eval7.Card(c) for c in FULL_DECK}


def position_labels(n, button):
    """Seat -> position label. 8-handed: SB BB UTG UTG1 MP HJ CO BTN."""
    labels = {}
    if n == 2:
        labels[button] = "BTN"            # heads-up: button posts the small blind
        labels[(button + 1) % n] = "BB"
        return labels
    labels[button] = "BTN"
    labels[(button + 1) % n] = "SB"
    labels[(button + 2) % n] = "BB"
    middle = MIDDLE_LABELS[-(n - 3):] if n > 3 else []
    for i, lab in enumerate(middle):
        labels[(button + 3 + i) % n] = lab
    return labels


def normalize_action(a):
    """Accept the many shapes an AI-written bot might return. Returns (kind, amount)."""
    kind, amount = None, 0
    if a is None:
        return ("invalid", 0)
    if isinstance(a, str):
        parts = a.strip().lower().replace("-", "_").split()
        if not parts:
            return ("invalid", 0)
        kind = parts[0]
        if len(parts) > 1:
            try:
                amount = int(float(parts[-1]))
            except ValueError:
                return ("invalid", 0)
    elif isinstance(a, (tuple, list)):
        if not a:
            return ("invalid", 0)
        kind = str(a[0]).lower()
        if len(a) > 1 and a[1] is not None:
            try:
                amount = int(a[1])
            except (TypeError, ValueError):
                return ("invalid", 0)
    elif isinstance(a, dict):
        kind = str(a.get("action", a.get("kind", a.get("type", "")))).lower()
        try:
            amount = int(a.get("amount", a.get("raise_to", 0)) or 0)
        except (TypeError, ValueError):
            return ("invalid", 0)
    elif hasattr(a, "kind"):
        kind = str(a.kind).lower()
        amount = int(getattr(a, "amount", 0) or 0)
    else:
        return ("invalid", 0)
    aliases = {"f": "fold", "x": "check", "c": "call", "b": "raise", "r": "raise",
               "bet": "raise", "raise_to": "raise", "raiseto": "raise",
               "all_in": "allin", "allin": "allin", "shove": "allin", "jam": "allin", "a": "allin"}
    kind = aliases.get(kind, kind)
    if kind not in ("fold", "check", "call", "raise", "allin"):
        return ("invalid", 0)
    return (kind, amount)


class Config:
    def __init__(self, sb=50, bb=100, stack=10000, time_limit=1.0, illegal="fold"):
        self.sb, self.bb, self.stack = sb, bb, stack
        self.time_limit = time_limit
        self.illegal = illegal   # 'fold' (strict) or 'clamp' (forgiving)


class HandResult:
    def __init__(self):
        self.net = {}            # pid -> chips won/lost this hand
        self.violations = []     # (pid, kind, detail)
        self.max_think = {}      # pid -> slowest reply this hand (s)
        self.showdown = False
        self.pot = 0


def play_hand(seat_bots, button, deck, cfg, hand_id, alias=None, log=None, stacks=None):
    """
    seat_bots: list of (pid, bot) in seat order. deck: list of card strings (shuffled).
    alias: optional pid -> name shown to other bots (for anonymous-opponent tests).
    log: optional list; a full record of the hand (holes, actions, result) is appended.
    """
    n = len(seat_bots)
    alias = alias or {pid: pid for pid, _ in seat_bots}
    res = HandResult()
    P = []
    for seat, (pid, bot) in enumerate(seat_bots):
        P.append({"seat": seat, "pid": pid, "bot": bot, "name": alias[pid],
                  "stack": stacks[seat] if stacks else cfg.stack, "bet": 0, "committed": 0,
                  "folded": False, "allin": False, "hole": None})
    pos = position_labels(n, button)
    for i in range(n):                       # deal starting left of the button
        P[(button + 1 + i) % n]["hole"] = deck[2 * i: 2 * i + 2]
    board_all = deck[2 * n: 2 * n + 5]
    board = []
    history = []

    def emit(event, only=None):
        for p in P:
            if only is not None and p is not only:
                continue
            try:
                p["bot"].observe(event)
            except Exception as e:  # observe errors never break the table
                res.violations.append((p["pid"], "observe_error", repr(e)[:120]))

    def pot_total():
        return sum(p["committed"] for p in P)

    def public_players():
        return [{"seat": p["seat"], "name": p["name"], "position": pos[p["seat"]],
                 "stack": p["stack"], "bet": p["bet"], "committed": p["committed"],
                 "folded": p["folded"], "allin": p["allin"]} for p in P]

    def put_in(p, amount):
        amount = min(amount, p["stack"])
        p["stack"] -= amount
        p["bet"] += amount
        p["committed"] += amount
        if p["stack"] == 0:
            p["allin"] = True
        return amount

    # ---- hand start
    if n == 2:
        sb_seat, bb_seat = button, (button + 1) % n
    else:
        sb_seat, bb_seat = (button + 1) % n, (button + 2) % n
    for p in P:
        emit({"type": "hand_start", "hand_id": hand_id, "button": button,
              "seats": [q["name"] for q in P], "positions": [pos[q["seat"]] for q in P],
              "my_seat": p["seat"], "my_name": p["name"], "hole": list(p["hole"]),
              "sb": cfg.sb, "bb": cfg.bb, "stack": cfg.stack}, only=p)
    for seat, amt in ((sb_seat, cfg.sb), (bb_seat, cfg.bb)):
        p = P[seat]
        pot_before = pot_total()
        put_in(p, amt)
        ev = {"type": "action", "hand_id": hand_id, "street": "preflop", "seat": seat,
              "player": p["name"], "position": pos[seat], "action": "blind",
              "amount": p["bet"], "added": p["bet"], "to_call": 0, "pot": pot_before, "current_bet": 0,
              "bet_before": 0, "allin": p["allin"]}
        history.append(ev)
        emit(ev)

    def n_live():
        return sum(1 for p in P if not p["folded"])

    def betting_round(street, first_seat):
        current_bet = max(p["bet"] for p in P)
        min_inc = cfg.bb
        order = [(first_seat + i) % n for i in range(n)]
        queue = [s for s in order if not P[s]["folded"] and not P[s]["allin"]]
        can_raise = {s: True for s in range(n)}
        acted = set()
        while queue:
            s = queue.pop(0)
            p = P[s]
            if p["folded"] or p["allin"]:
                continue
            if n_live() == 1:
                return
            to_call = current_bet - p["bet"]
            others_can_act = any(not q["folded"] and not q["allin"] for q in P if q is not p)
            if to_call == 0 and not others_can_act:
                continue
            max_raise_to = p["bet"] + p["stack"]
            raise_ok = can_raise[s] and others_can_act and max_raise_to > current_bet
            min_raise_to = min(current_bet + min_inc, max_raise_to) if raise_ok else None
            legal = ["fold"] + (["check"] if to_call == 0 else ["call"]) + (["raise"] if raise_ok else [])
            state = {
                "hand_id": hand_id, "street": street, "board": list(board), "hole": list(p["hole"]),
                "my_seat": s, "my_name": p["name"], "position": pos[s], "button": button,
                "pot": pot_total(), "to_call": min(to_call, p["stack"]),
                "current_bet": current_bet, "my_bet": p["bet"], "my_stack": p["stack"],
                "min_raise_to": min_raise_to, "max_raise_to": max_raise_to if raise_ok else None,
                "legal": legal, "sb": cfg.sb, "bb": cfg.bb, "starting_stack": cfg.stack,
                "players": public_players(), "history": list(history),
                "n_live": n_live(),
            }
            t0 = time.perf_counter()
            raw, err = None, None
            try:
                raw = p["bot"].act(state)
            except Exception as e:
                err = repr(e)[:160]
            dt = time.perf_counter() - t0
            res.max_think[p["pid"]] = max(res.max_think.get(p["pid"], 0.0), dt)
            kind, amount = normalize_action(raw)
            fallback = "check" if to_call == 0 else "fold"

            if err is not None:
                res.violations.append((p["pid"], "exception", err))
                kind, amount = fallback, 0
            elif dt > cfg.time_limit:
                res.violations.append((p["pid"], "timeout", f"{dt*1000:.0f} ms"))
                kind, amount = fallback, 0
            else:
                # resolve easy aliases first
                if kind == "call" and to_call == 0:
                    kind = "check"
                if kind == "allin":
                    if raise_ok:
                        kind, amount = "raise", max_raise_to
                    else:
                        kind = "check" if to_call == 0 else "call"
                bad = None
                if kind == "invalid":
                    bad = f"unparseable action {raw!r}"
                elif kind == "check" and to_call > 0:
                    bad = f"check facing {to_call}"
                elif kind == "raise":
                    if not raise_ok:
                        bad = f"raise not allowed (raise to {amount})"
                    elif not (min_raise_to <= amount <= max_raise_to):
                        bad = f"raise to {amount} outside [{min_raise_to}, {max_raise_to}]"
                if bad:
                    res.violations.append((p["pid"], "illegal", bad))
                    if cfg.illegal == "clamp" and kind == "raise" and raise_ok:
                        amount = max(min_raise_to, min(amount, max_raise_to))
                    elif cfg.illegal == "clamp" and kind == "check":
                        kind = "fold"
                    else:
                        kind, amount = fallback, 0

            pot_before, bet_before, cb_before = pot_total(), p["bet"], current_bet
            stack_before = p["stack"]
            if kind == "fold":
                p["folded"] = True
                acted.add(s)
                amt_out = 0
            elif kind == "check":
                acted.add(s)
                amt_out = 0
            elif kind == "call":
                put_in(p, to_call)
                acted.add(s)
                amt_out = p["bet"]
            else:  # raise to `amount`
                put_in(p, amount - p["bet"])
                inc = p["bet"] - current_bet
                full = inc >= min_inc
                if full:
                    min_inc = inc
                    acted = {s}
                    can_raise = {x: True for x in range(n)}
                else:   # short all-in: players who already acted may only call or fold
                    for x in acted:
                        can_raise[x] = False
                    acted.add(s)
                current_bet = p["bet"]
                amt_out = p["bet"]
                queue = [(s + i) % n for i in range(1, n)]
                queue = [x for x in queue if not P[x]["folded"] and not P[x]["allin"]]
            ev = {"type": "action", "hand_id": hand_id, "street": street, "seat": s,
                  "player": p["name"], "position": pos[s], "action": kind, "amount": amt_out,
                  "added": p["bet"] - bet_before, "to_call": min(to_call, stack_before),
                  "pot": pot_before, "current_bet": cb_before, "bet_before": bet_before,
                  "allin": p["allin"]}
            history.append(ev)
            emit(ev)

    # ---- streets
    preflop_first = (bb_seat + 1) % n
    betting_round("preflop", preflop_first)
    for street, ncards in (("flop", 3), ("turn", 1), ("river", 1)):
        if n_live() == 1:
            break
        board.extend(board_all[len(board): len(board) + ncards])
        for p in P:
            p["bet"] = 0
        ev = {"type": "street", "hand_id": hand_id, "street": street, "board": list(board),
              "pot": pot_total()}
        history.append(ev)
        emit(ev)
        active = [p for p in P if not p["folded"] and not p["allin"]]
        if len(active) >= 2:
            betting_round(street, (button + 1) % n)

    # ---- settle
    total = pot_total()
    res.pot = total
    winnings = {p["pid"]: 0 for p in P}
    live = [p for p in P if not p["folded"]]
    shown = {}
    if len(live) == 1:
        winnings[live[0]["pid"]] = total
    else:
        res.showdown = True
        while len(board) < 5:
            board.append(board_all[len(board)])
        bcards = [_CARD[c] for c in board]
        score = {p["seat"]: eval7.evaluate([_CARD[c] for c in p["hole"]] + bcards) for p in live}
        for p in live:
            shown[p["name"]] = list(p["hole"])
        levels = sorted(set(p["committed"] for p in P if p["committed"] > 0))
        prev, carry = 0, 0
        order_from_button = [(button + 1 + i) % n for i in range(n)]
        last_winners = None
        for lvl in levels:
            amt = sum(min(p["committed"], lvl) - min(p["committed"], prev) for p in P) + carry
            elig = [p for p in live if p["committed"] >= lvl]
            prev = lvl
            if not elig:
                carry = amt
                continue
            carry = 0
            best = max(score[p["seat"]] for p in elig)
            winners = [p for p in elig if score[p["seat"]] == best]
            winners.sort(key=lambda q: order_from_button.index(q["seat"]))
            share, rem = divmod(amt, len(winners))
            for i, w in enumerate(winners):
                winnings[w["pid"]] += share + (1 if i < rem else 0)
            last_winners = winners
        if carry and last_winners:
            winnings[last_winners[0]["pid"]] += carry
        ev = {"type": "showdown", "hand_id": hand_id, "board": list(board), "shown": shown,
              "hand_types": {P[s]["name"]: eval7.handtype(v) for s, v in score.items()}}
        history.append(ev)
        emit(ev)
    for p in P:
        res.net[p["pid"]] = winnings[p["pid"]] - p["committed"]
    assert sum(res.net.values()) == 0, "chip conservation broken"
    if log is not None:
        log.append({"hand_id": hand_id, "button": button,
                    "seats": [{"pid": p["pid"], "name": p["name"], "position": pos[p["seat"]],
                               "hole": p["hole"]} for p in P],
                    "board": list(board), "history": history, "net": dict(res.net),
                    "violations": list(res.violations)})
    emit({"type": "hand_end", "hand_id": hand_id, "board": list(board), "pot": total,
          "net": {P[i]["name"]: res.net[P[i]["pid"]] for i in range(n)}, "shown": shown})
    return res
