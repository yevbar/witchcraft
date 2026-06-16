"""effect_handlers/lands.py — §116.2a one-shot 'play an additional land this turn'.

The one-shot land-permission rider (Explore, Sakura-Tribe Scout's payoff, etc.): card_effect
('play', '-', 'you', 'additional_land_this_turn'). It bumps the controller's extra-land grant for this
turn (driver._grant_extra_land), which the driver's §305.2 land-drop loop (driver._develop_mana) then
honors — so the player actually gets to put a second land onto the battlefield. (The CONTINUOUS version,
Exploration/Azusa's static_player 'extra_land_per_turn', is wired in driver._static_extra_lands but only
fires once static_player facts are loaded into the game state.)
"""

from __future__ import annotations

from effect_handlers import encoder, applier


@encoder("play")
def encode_play(verb, amt, tgt, extra):
    # only the clean 'play an additional land this turn' rider; other 'play <card>' forms abstain.
    if str(extra) == "additional_land_this_turn":
        return ("extra_land", 1, "controller")
    return None


@applier("extra_land")
def apply_extra_land(D, state, a, n, tgt, src, ctrl):
    D._grant_extra_land(state, ctrl, int(n) or 1)
    print(f"    {a}: {ctrl} may play {n} additional land(s) this turn")
