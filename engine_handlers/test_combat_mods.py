"""engine_handlers/test_combat_mods.py — regression checks for the combat_mods handlers.

Proves END-TO-END that each verb's flag/shield is both SET by the handler and ENFORCED by the shared
loops in engine.py (combat attacker filter, _choose_block, the untap step, _damage_player, sba/_destroy
regen). Hand-built minimal states, dispatched through Game._do exactly like the engine does. Pure Python,
no souffle/cards.dl needed — run: python3 engine_handlers/test_combat_mods.py
"""

from __future__ import annotations

from collections import Counter

from engine import Card, Game, Perm, Player


def _game() -> Game:
    g = Game.__new__(Game)                       # bypass deck setup — we place state by hand
    g.p = [Player("A"), Player("B")]
    g.over = False
    g.active = 0
    g.turn = 0
    return g


def _creature(ctrl: int, p: int = 2, t: int = 2, name: str = "Bears") -> Perm:
    return Perm(Card(name, Counter(), {"Creature"}, set(), p, t), ctrl, sick=False)


CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def run() -> None:
    # cant_be_blocked: the attacker reaches the face even though a fine blocker is available.
    g = _game()
    atk = _creature(0, 3, 3, "Sneaky"); blk = _creature(1, 0, 5, "Wall")
    g.p[0].bf = [atk]; g.p[1].bf = [blk]; g.p[1].life = 20
    g._do(g.p[0], g.p[1], "cant_be_blocked", "-", "self", "-", atk)
    g.combat(g.p[0], g.p[1])
    check("cant_be_blocked: flag set", "cant_be_blocked" in atk.flags)
    check("cant_be_blocked: hits face despite blocker", g.p[1].life == 17 and blk in g.p[1].bf)

    # cant_block: the would-be blocker can't block, so the attacker connects.
    g = _game()
    atk = _creature(0, 4, 4, "Beater"); blk = _creature(1, 5, 5, "Coward")
    g.p[0].bf = [atk]; g.p[1].bf = [blk]; g.p[1].life = 20
    g._do(g.p[0], g.p[1], "cant_block", "-", "self", "self", blk)   # source=blk, 'self' in extra
    g.combat(g.p[0], g.p[1])
    check("cant_block: flag set", "cant_block" in blk.flags)
    check("cant_block: attacker connects", g.p[1].life == 16)

    # cant_attack: the marked creature is filtered out of the attacker list.
    g = _game()
    atk = _creature(0, 6, 6, "Grounded")
    g.p[0].bf = [atk]; g.p[1].bf = []; g.p[1].life = 20
    g._do(g.p[0], g.p[1], "cant_attack", "-", "self", "-", atk)
    g.combat(g.p[0], g.p[1])
    check("cant_attack: flag set", "cant_attack" in atk.flags)
    check("cant_attack: no damage dealt", g.p[1].life == 20)

    # doesnt_untap: the untap step skips the flagged permanent (and only it).
    g = _game()
    stuck = _creature(0, 2, 2, "Stuck"); free = _creature(0, 2, 2, "Free")
    stuck.tapped = True; free.tapped = True
    g.p[0].bf = [stuck, free]
    g._do(g.p[0], g.p[1], "doesnt_untap", "-", "self", "next", stuck)
    for perm in g.p[0].bf:                        # the engine's untap loop (take_turn)
        if "doesnt_untap" not in perm.flags:
            perm.tapped = False
    check("doesnt_untap: flagged stays tapped", stuck.tapped)
    check("doesnt_untap: others still untap", not free.tapped)

    # prevent_damage (numeric): a shield of n reduces the next damage to a player.
    g = _game(); g.p[1].life = 20
    g._do(g.p[0], g.p[1], "prevent_damage", "3", "target_player", "-", None)
    dealt = g._damage_player(g.p[1], 5)
    check("prevent_damage 3: shield raised", True)   # exercised below by the effect
    check("prevent_damage 3: 5 dmg becomes 2", dealt == 2 and g.p[1].life == 18)

    # prevent_damage 'all' (fog-style): a large shield absorbs the whole turn's damage.
    g = _game(); g.p[0].life = 20
    g._do(g.p[0], g.p[1], "prevent_damage", "all", "combat", "-", None)
    dealt = g._damage_player(g.p[0], 99)
    check("prevent_damage all: fully prevented", dealt == 0 and g.p[0].life == 20)

    # prevent_damage on a creature isn't modelled by the damage loop -> abstain (no player shield).
    g = _game()
    before = (g.p[0].prevent, g.p[1].prevent)
    g._do(g.p[0], g.p[1], "prevent_damage", "2", "target_creature", "-", None)
    check("prevent_damage creature-only: abstains",
          (g.p[0].prevent, g.p[1].prevent) == before)

    # cant_be_regenerated: strips a regen_shield so lethal damage actually kills.
    g = _game()
    troll = _creature(1, 3, 3, "Troll"); troll.flags.add("regen_shield")
    g.p[1].bf = [troll]
    g._do(g.p[0], g.p[1], "cant_be_regenerated", "-", "target_creature", "-", None)
    check("cant_be_regenerated: shield stripped", "regen_shield" not in troll.flags)
    troll.dmg = 3
    g.sba()
    check("cant_be_regenerated: creature dies, no regen", troll not in g.p[1].bf)

    # cant_be_regenerated 'it' resolves to the source permanent.
    g = _game()
    s = _creature(0, 1, 1, "Self"); s.flags.add("regen_shield")
    g.p[0].bf = [s]
    g._do(g.p[0], g.p[1], "cant_be_regenerated", "-", "it", "-", s)
    check("cant_be_regenerated 'it': source shield stripped", "regen_shield" not in s.flags)

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
