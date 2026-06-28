"""test_society_of_control.py — the SocietyOfControlPlayer control bot (mtg/society_of_control.py).

Focus: burn_choice — cast a CREATURE-TARGETING damage spell ONLY when it would KILL an opponent creature
(strictly target creature, not 'any target'; never fired speculatively; weighted by the biggest threat it can
remove). Damage spells pick their target at resolution, so burn_choice is a CAST-TIME decision keyed on whether
a lethal target exists. Run as a script; exits non-zero on any failure.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from mtg.game import Game
from mtg.society_of_control import SocietyOfControlPlayer

_fails = 0


def check(name: str, cond: bool) -> None:
    global _fails
    print(f"  {'ok  ' if cond else 'FAIL'} {name}")
    if not cond:
        _fails += 1


def _game(*, hand, effects, creatures):
    """A minimal engine state: `hand` = [(inst, slug)], `effects` = {slug: (amount, scope)} deal_damage facts,
    `creatures` = [(power, toughness, controller)] on the battlefield."""
    st = {
        "is_player": {("alice",), ("bob",)}, "life": {("alice", 20), ("bob", 20)},
        "active_player": {("alice",)}, "has_priority": {("alice",)}, "current_step": {("precombat_main",)},
        "in_hand": {("alice", i) for i, _ in hand},
        "instance_of": {(i, s) for i, s in hand},
        "card_ability": {(s, "a0", "spell") for s in effects},
        "card_effect": {(s, "a0", 0, "deal_damage", str(a), scope, "-", "-") for s, (a, scope) in effects.items()},
        "on_battlefield": set(), "printed_control": set(), "printed_type": set(),
        "printed_power": set(), "printed_toughness": set(),
    }
    for n, (p, t, ctrl) in enumerate(creatures):
        cid = f"c{n}"
        st["instance_of"] |= {(cid, "grizzly_bears")}
        st["on_battlefield"] |= {(cid,)}
        st["printed_control"] |= {(ctrl, cid)}
        st["printed_type"] |= {(cid, "creature")}
        st["printed_power"] |= {(cid, p)}
        st["printed_toughness"] |= {(cid, t)}
    return Game.from_state(st)


def _burn(g, card):
    p = SocietyOfControlPlayer(); p.bind(g, "alice")
    return p.burn_choice(g, NS(kind="cast", card=NS(id=card), choices={}))


def run() -> None:
    BOMBARD = {"bombard": (4, "target_creature")}                 # 4 dmg, strictly target creature
    BOLT = {"lightning_bolt": (3, "any_target")}                  # 3 dmg, ANY target (face burn)

    g = _game(hand=[("b1", "bombard")], effects=BOMBARD, creatures=[(2, 2, "bob"), (5, 5, "bob")])
    check("burn_choice fires on a lethal target (4 dmg kills the 2/2), weighted by its power",
          _burn(g, "b1") == 1.0 + 2)
    check("burn_choice ignores the un-killable 5/5 (scores by the killable one)", _burn(g, "b1") == 3.0)

    g = _game(hand=[("b1", "bombard")], effects=BOMBARD, creatures=[(2, 2, "bob"), (4, 4, "bob")])
    check("burn_choice weights by the BIGGEST killable threat (4/4 over 2/2)", _burn(g, "b1") == 1.0 + 4)

    g = _game(hand=[("b1", "bombard")], effects=BOMBARD, creatures=[(5, 5, "bob")])
    check("burn_choice does NOT fire when nothing is killable (no speculative cast)", _burn(g, "b1") == float("-inf"))

    g = _game(hand=[("b1", "bombard")], effects=BOMBARD, creatures=[(2, 2, "alice")])
    check("burn_choice ignores OUR own creatures", _burn(g, "b1") == float("-inf"))

    g = _game(hand=[("lb", "lightning_bolt")], effects=BOLT, creatures=[(2, 2, "bob")])
    check("burn_choice EXCLUDES 'any target' spells (face burn, handled elsewhere)",
          _burn(g, "lb") == float("-inf"))

    g = _game(hand=[("b1", "bombard")], effects={}, creatures=[(2, 2, "bob")])
    check("burn_choice is inert when card rules aren't loaded (no card_effect -> -inf)",
          _burn(g, "b1") == float("-inf"))

    print(f"\n{'ALL PASS' if not _fails else str(_fails) + ' FAILED'}")
    raise SystemExit(1 if _fails else 0)


if __name__ == "__main__":
    run()
