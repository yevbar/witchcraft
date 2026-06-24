"""witchcraft.wincon — the enumerable §104 win/loss AXES, and which ones a seat's deck can actually reach.

The engine declares the axes in datalog/engine_rules.dl (loss_threshold + the loses_game/wins_game rules):
life_zero, poison_ten, commander_damage, deckout (draw-from-empty), and the alt-win "you win the game". This
mirrors them as a `WinCon` enum and reads a seat's cards (via the engine's own card_keyword / card_effect /
card_power facts) to report which axes that deck can pursue — so a search or a player can ignore the rest.

Soundness rule: an axis is reported reachable when a card *can* contribute to it; LIFE_ZERO is kept whenever
any damage/drain or creature exists (almost always), while POISON/DECKOUT/WIN_GAME are only added on a concrete
enabler — and DECKOUT only on an ACTIVE one (mill or forced-opponent-draw), since the 40-turn passive grind is
not a line anything searches toward. False positives (keeping an unreachable axis) are harmless; the design
never drops a reachable one on a recognized card.
"""

from __future__ import annotations

import enum


class WinCon(enum.Enum):
    """A way to win — i.e. a way to make an opponent meet a §104.3 loss condition (or an alt-win)."""

    LIFE_ZERO = "life_zero"                 # §104.3a — opponent's life to 0 (combat damage / burn / drain)
    POISON_TEN = "poison_ten"               # §104.3c / §122 — 10 poison counters (infect / toxic / poisonous)
    COMMANDER_DAMAGE = "commander_damage"   # §903.10a — 21 combat damage from a single commander
    DECKOUT = "deckout"                     # §104.3c — opponent draws from an empty library (mill / forced draw)
    WIN_GAME = "win_game"                   # alt-win — a "you win the game" effect (Thassa's Oracle, Approach, …)


_POISON_KW = {"infect", "toxic", "poisonous"}


def _seat_identities(state: dict, seat: str) -> set:
    """The set of card IDENTITIES (oracle slugs) the seat has across its zones — its deck. card_* facts are
    keyed by identity, so this maps the seat's instances (library/hand/battlefield) through `instance_of`."""
    inst_of = {i: c for (i, c) in state.get("instance_of", ())}
    ids: set = set()
    for rel in ("in_library", "in_hand"):                       # (player, instance)
        for (p, i) in state.get(rel, ()):
            if p == seat:
                ids.add(inst_of.get(i, i))
    for (p, i) in state.get("printed_control", ()):             # battlefield, by controller
        if p == seat:
            ids.add(inst_of.get(i, i))
    return ids


def reachable(state: dict, seat: str) -> set:
    """The `WinCon`s the `seat`'s deck can pursue, read from the engine's card facts (card_keyword / card_effect
    / card_power). See the module docstring for the soundness rule."""
    ids = _seat_identities(state, seat)
    kw_by_id: dict = {}
    for (cid, kw) in state.get("card_keyword", ()):
        kw_by_id.setdefault(cid, set()).add(kw)
    eff_by_id: dict = {}
    for r in state.get("card_effect", ()):                      # (id, aid, seq, VERB, amt, TGT, extra, cond)
        eff_by_id.setdefault(r[0], []).append((r[3], str(r[5]) if len(r) > 5 else ""))
    pow_by_id = {cid: p for (cid, p) in state.get("card_power", ())}

    out: set = set()
    for cid in ids:
        kws = kw_by_id.get(cid, set())
        verbs = eff_by_id.get(cid, [])                          # [(verb, tgt), …]
        if kws & _POISON_KW or any(v == "put_counter" and "poison" in t for (v, t) in verbs):
            out.add(WinCon.POISON_TEN)
        if any(v == "mill" or (v == "draw" and "opp" in t) for (v, t) in verbs):   # ACTIVE deckout only
            out.add(WinCon.DECKOUT)
        if any(v == "win_game" for (v, _t) in verbs):
            out.add(WinCon.WIN_GAME)
        # §120.3b: an INFECT creature's combat damage is poison, not life — so power only feeds LIFE_ZERO when
        # the creature isn't infect (toxic/poisonous still deal normal life damage AND add poison).
        if (pow_by_id.get(cid, 0) > 0 and "infect" not in kws) or any(v in ("deal_damage", "lose_life") for (v, _t) in verbs):
            out.add(WinCon.LIFE_ZERO)
    if state.get("is_commander"):                               # §903 — only a commander game has this axis
        out.add(WinCon.COMMANDER_DAMAGE)
    return out
