"""Example of a generated bot dropped into bots/external/. Shape only: replace with real
output from ChatGPT / Claude / Gemini (see prompts/field_prompt.md)."""
import random
import eval7

RANKS = "23456789TJQKA"


class MyBot:
    def __init__(self, seed=None):
        self.rng = random.Random(seed)

    def observe(self, event):
        pass

    def equity(self, hole, board, n=300):
        hero = [eval7.Card(c) for c in hole]
        b = [eval7.Card(c) for c in board]
        deck = [c for c in eval7.Deck().cards if c not in hero and c not in b]
        wins = 0.0
        for _ in range(n):
            s = self.rng.sample(deck, 2 + 5 - len(b))
            full = b + s[2:]
            me, op = eval7.evaluate(hero + full), eval7.evaluate(s[:2] + full)
            wins += 1 if me > op else 0.5 if me == op else 0
        return wins / n

    def act(self, state):
        eq = self.equity(state["hole"], state["board"])
        to_call, pot = state["to_call"], state["pot"]
        if eq > 0.7 and "raise" in state["legal"]:
            amt = max(state["min_raise_to"], min(state["max_raise_to"], state["current_bet"] + pot))
            return ("raise", amt)
        if to_call == 0:
            return "check"
        return "call" if eq > to_call / (pot + to_call) else "fold"
