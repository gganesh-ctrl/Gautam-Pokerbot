"""
The bot interface. Mirror whatever the real tournament protocol is in a thin adapter;
keep strategy code talking to this shape so the lab and the event share one brain.

state passed to act():
    hand_id, street ('preflop'|'flop'|'turn'|'river'), board ['Qh','7c','2s'], hole ['As','Kd']
    my_seat, my_name, position ('UTG','UTG1','MP','HJ','CO','BTN','SB','BB'), button
    pot            chips in the middle including all bets this street
    to_call        chips you must add to call (already capped at your stack)
    current_bet    highest total bet this street;  my_bet = your total bet this street
    my_stack       chips behind
    min_raise_to / max_raise_to   legal "raise to" window (None if you cannot raise)
    legal          subset of ['fold','check','call','raise']
    players        list of {seat, name, position, stack, bet, committed, folded, allin}
    history        every public event so far this hand (blinds, actions, streets)
    sb, bb, starting_stack, n_live

act() returns: 'fold' | 'check' | 'call' | ('raise', raise_to_total) | 'allin'

observe() receives: hand_start (with your hole), action, street, showdown, hand_end.
"""


class Bot:
    name = "bot"

    def __init__(self, **params):
        self.params = params

    def observe(self, event):
        pass

    def act(self, state):
        return "check" if state["to_call"] == 0 else "fold"
