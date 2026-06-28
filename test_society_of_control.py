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
    `creatures` = [(power, toughness, controller)] or [(power, toughness, controller, is_commander)] battlefield."""
    st = {
        "is_player": {("alice",), ("bob",)}, "life": {("alice", 20), ("bob", 20)},
        "active_player": {("alice",)}, "has_priority": {("alice",)}, "current_step": {("precombat_main",)},
        "in_hand": {("alice", i) for i, _ in hand},
        "instance_of": {(i, s) for i, s in hand},
        "card_ability": {(s, "a0", "spell") for s in effects},
        "card_effect": {(s, "a0", 0, "deal_damage", str(a), scope, "-", "-") for s, (a, scope) in effects.items()},
        "on_battlefield": set(), "printed_control": set(), "printed_type": set(),
        "printed_power": set(), "printed_toughness": set(), "is_commander": set(),
    }
    for n, spec in enumerate(creatures):
        p, t, ctrl = spec[:3]
        cid = f"c{n}"
        st["instance_of"] |= {(cid, "grizzly_bears")}
        st["on_battlefield"] |= {(cid,)}
        st["printed_control"] |= {(ctrl, cid)}
        st["printed_type"] |= {(cid, "creature")}
        st["printed_power"] |= {(cid, p)}
        st["printed_toughness"] |= {(cid, t)}
        if len(spec) > 3 and spec[3]:
            st["is_commander"] |= {(cid,)}
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

    # COMMANDER RESERVE: hold the only commander-answer; spend it only with a backup in hand.
    g = _game(hand=[("b1", "bombard")], effects=BOMBARD, creatures=[(3, 3, "bob", True)])  # commander, only answer
    check("burn_choice reserves our ONLY commander-answer (don't spend the last one)", _burn(g, "b1") == float("-inf"))

    g = _game(hand=[("b1", "bombard"), ("b2", "bombard")], effects=BOMBARD, creatures=[(3, 3, "bob", True)])
    check("burn_choice kills the commander when a BACKUP answer is in hand", _burn(g, "b1") == 1.0 + 3)

    g = _game(hand=[("b1", "bombard")], effects=BOMBARD, creatures=[(5, 5, "bob", True), (2, 2, "bob")])
    check("commander un-killable by this spell -> reserve rule off, kills the 2/2", _burn(g, "b1") == 1.0 + 2)

    g = _game(hand=[("b1", "bombard")], effects=BOMBARD, creatures=[(3, 3, "bob", True), (2, 2, "bob")])
    check("only commander-answer is held even with another creature killable (reserve wins)",
          _burn(g, "b1") == float("-inf"))

    # FORCED-WIN TAKE-OVER: a lethal line this turn is taken over any positional play.
    def _burn_to_face(opp_life, dmg=3, mana=5):
        # both players keep a library so a non-lethal sim doesn't auto-advance into a spurious deck-out loss
        lib = {(pl, f"{pl}_lib{i}") for pl in ("alice", "bob") for i in range(20)}
        st = {
            "is_player": {("alice",), ("bob",)}, "life": {("alice", 20), ("bob", opp_life)},
            "active_player": {("alice",)}, "has_priority": {("alice",)}, "current_step": {("precombat_main",)},
            "in_hand": {("alice", "bolt_1")}, "in_library": lib,
            "instance_of": {("bolt_1", "lightning_bolt")} | {(c, "forest") for (_p, c) in lib},
            "card_ability": {("lightning_bolt", "a0", "spell")},
            "card_effect": {("lightning_bolt", "a0", 0, "deal_damage", str(dmg), "any_target", "-", "-")},
            "spell_type": {("bolt_1", "instant")}, "free_grant": {("alice", "bolt_1")},
            "mana_available": {("alice", mana)},
        }
        g = Game.from_state(st); p = SocietyOfControlPlayer(); p.bind(g, "alice")
        return g, p

    g, p = _burn_to_face(2)                                       # 3-damage bolt, opponent at 2 -> lethal
    check("is_win_forceable True when a lethal burn is available", p.is_win_forceable(g) is True)
    win = p.force_win(g)
    check("force_win returns the lethal cast move", getattr(win, "kind", None) == "cast")
    check("choose_move TAKES the forced win (not a positional play)",
          getattr(p.choose_move(g), "kind", None) == "cast")

    g, p = _burn_to_face(5)                                       # 3-damage bolt, opponent at 5 -> NOT lethal
    check("is_win_forceable False when no lethal line exists", p.is_win_forceable(g) is False)
    check("force_win returns None when not lethal", p.force_win(g) is None)

    g, p = _burn_to_face(40, mana=0)                              # out of reach -> the cheap gate skips the scan
    check("force_win None (and gate skips) when the opponent is out of reach", p.force_win(g) is None)

    # choose_x placeholder: lethal preferred when affordable, else max affordable
    _, p = _burn_to_face(2)
    check("choose_x picks the lethal value when affordable", p.choose_x(g, lethal=4, affordable=6) == 4)
    check("choose_x falls back to max affordable when lethal is out of reach", p.choose_x(g, lethal=9, affordable=6) == 6)

    print(f"\n{'ALL PASS' if not _fails else str(_fails) + ' FAILED'}")
    raise SystemExit(1 if _fails else 0)


if __name__ == "__main__":
    run()
