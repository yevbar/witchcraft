"""effect_handlers/turns.py — EXTRA-TURN effects (§500.7) — controller-scoped.

Own the cards.dl verb:
  - extra_turn — 'Take an extra turn after this one' (Time Sieve, Final Fortune, Last Chance, Time Warp,
                 Temporal Manipulation, …). amt is the COUNT of extra turns (usually 1); tgt is the player
                 who takes them ('you' = the controller).

MODEL — the driver's turn loop (driver.play_game / env._advance_one) consults driver._next_active_player
at turn-pass time: when a player has a pending extra-turn marker in state['_extra_turns'][player], they
keep the turn instead of passing (§500.7 — extra turns are taken by that player before the turn moves on).
So resolving extra_turn just BUMPS that marker; the loop does the rest. We resolve only the clean,
choice-free controller form ('you take an extra turn') with a fixed integer count. 'Target player takes an
extra turn' / variable counts ABSTAIN (a choice / count the engine can't supply).

The §500.7 'after this one' chaining and the Final Fortune style 'at the beginning of that turn's end step
you lose the game' rider are SEPARATE clauses (their own card_effect rows: extra_turn + lose_game), each
resolved independently — extra_turn just grants the turn; the rider's lose_game is owned by its own handler.

FAITHFUL-OR-ABSTAIN: encode -> None for anything we can't resolve correctly. See effect_handlers/__init__.py
for the @encoder / @applier contract and the driver helpers reachable on D.
"""

from __future__ import annotations

from effect_handlers import encoder, applier

_SELF_TGT = {"you", "self", "its_controller", "controller", "-", ""}
# 'TARGET player takes an extra turn' (Time Warp, Capture of Jingzhou): the caster chooses the player —
# choosing THEMSELVES is a legal, beneficial, deterministic target, so we resolve it to the controller
# (faithful: the controller is always a legal target of 'target player').
_TARGET_PLAYER = {"target_player", "a_target_player"}


def _int(amt):
    s = str(amt)
    return int(s) if s.isdigit() else None


@encoder("extra_turn")
def _encode_extra_turn(verb, amt, tgt, extra):
    if str(tgt) not in _SELF_TGT and str(tgt) not in _TARGET_PLAYER:
        return None                                         # 'each opponent' / a specific other player -> abstain
    n = _int(amt)
    n = 1 if n is None else n                                # 'take an extra turn' with no explicit count = 1
    if n < 1:
        return None
    return ("extra_turn", n, "controller")


@applier("extra_turn")
def _apply_extra_turn(D, state, a, n, tgt, src, ctrl):
    """§500.7 — the controller takes n extra turns after this one. Record the markers; the driver's turn
    loop (driver._next_active_player) keeps the turn with this player until the markers are consumed."""
    extra = state.setdefault("_extra_turns", {})
    extra[ctrl] = extra.get(ctrl, 0) + n
    print(f"    {a}: {ctrl} will take {n} extra turn(s) after this one (§500.7)")
