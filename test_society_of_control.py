"""test_society_of_control.py — the SocietyOfControlPlayer control bot (mtg/society_of_control.py).

Focus: burn_choice — cast a CREATURE-TARGETING damage spell ONLY when it would KILL an opponent creature
(strictly target creature, not 'any target'; never fired speculatively; weighted by the biggest threat it can
remove). Damage spells pick their target at resolution, so burn_choice is a CAST-TIME decision keyed on whether
a lethal target exists. Run as a script; exits non-zero on any failure.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from mtg.game import Game
from mtg.predicates import is_mana_rock
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


def _burn(g, card, target=None):
    """burn_choice score for casting `card`; `target` (a creature instance id) selects the per-target cast
    variant the engine would enumerate (None = no cast-time target -> scored by the best creature on board)."""
    p = SocietyOfControlPlayer(); p.bind(g, "alice")
    choices = {"target": target} if target is not None else {}
    return p.burn_choice(g, NS(kind="cast", card=NS(id=card), choices=choices))


def run() -> None:
    BOMBARD = {"bombard": (4, "target_creature")}                 # 4 dmg, strictly target creature
    BOLT = {"lightning_bolt": (3, "any_target")}                  # 3 dmg, ANY target (face burn)
    NEG = float("-inf")

    # ── FIRES only on an actual kill ───────────────────────────────────────────────────────────────────────
    g = _game(hand=[("b1", "bombard")], effects=BOMBARD, creatures=[(2, 2, "bob"), (5, 5, "bob")])
    check("burn_choice fires on a lethal target (4 dmg kills the 2/2)", _burn(g, "b1") > 0)
    check("burn_choice -inf at the un-killable 5/5 variant (5 toughness > 4 dmg)", _burn(g, "b1", "c1") == NEG)
    check("burn_choice fires at the killable 2/2 variant", _burn(g, "b1", "c0") > 0)

    g = _game(hand=[("b1", "bombard")], effects=BOMBARD, creatures=[(5, 5, "bob")])
    check("burn_choice does NOT fire when nothing is killable (no speculative cast)", _burn(g, "b1") == NEG)

    g = _game(hand=[("b1", "bombard")], effects=BOMBARD, creatures=[(2, 2, "alice")])
    check("burn_choice ignores OUR own creatures", _burn(g, "b1") == NEG)

    g = _game(hand=[("lb", "lightning_bolt")], effects=BOLT, creatures=[(2, 2, "bob")])
    check("burn_choice EXCLUDES 'any target' spells (face burn, handled elsewhere)", _burn(g, "lb") == NEG)

    g = _game(hand=[("b1", "bombard")], effects={}, creatures=[(2, 2, "bob")])
    check("burn_choice is inert when card rules aren't loaded (no card_effect -> -inf)", _burn(g, "b1") == NEG)

    # ── TARGET PRIORITY: commander > power > toughness > arbitrary ──────────────────────────────────────────
    # commander beats a far bigger non-commander (backup answer in hand so the reserve rule doesn't hold it)
    g = _game(hand=[("b1", "bombard"), ("b2", "bombard")], effects=BOMBARD,
              creatures=[(1, 1, "bob", True), (4, 4, "bob")])
    check("priority: COMMANDER (1/1) outranks a bigger non-commander (4/4)", _burn(g, "b1", "c0") > _burn(g, "b1", "c1"))

    # among non-commanders, higher POWER wins even with lower toughness
    g = _game(hand=[("b1", "bombard")], effects=BOMBARD, creatures=[(3, 1, "bob"), (1, 4, "bob")])
    check("priority: higher POWER (3/1) outranks higher toughness (1/4)", _burn(g, "b1", "c0") > _burn(g, "b1", "c1"))

    # equal power -> higher TOUGHNESS wins
    g = _game(hand=[("b1", "bombard")], effects=BOMBARD, creatures=[(2, 2, "bob"), (2, 4, "bob")])
    check("priority: equal power -> higher TOUGHNESS (2/4 over 2/2)", _burn(g, "b1", "c1") > _burn(g, "b1", "c0"))

    # equal power AND toughness -> both fire, broken by a stable arbitrary jitter (distinct, deterministic)
    g = _game(hand=[("b1", "bombard")], effects=BOMBARD, creatures=[(2, 2, "bob"), (2, 2, "bob")])
    s0, s1 = _burn(g, "b1", "c0"), _burn(g, "b1", "c1")
    check("priority: identical P/T both fire", s0 > 0 and s1 > 0)
    check("priority: identical P/T broken by an arbitrary (distinct) jitter", s0 != s1)
    check("priority: the jitter is deterministic (stable across calls)", _burn(g, "b1", "c0") == s0)

    # no cast-time target -> scored by the BEST creature on board (commander here)
    g = _game(hand=[("b1", "bombard")], effects=BOMBARD, creatures=[(4, 4, "bob"), (1, 1, "bob", True)])
    check("no-target fallback scores by the best creature (the commander)",
          _burn(g, "b1") == _burn(g, "b1", "c1"))

    # ── COMMANDER RESERVE: hold the only commander-answer; spend it only with a backup in hand ──────────────
    g = _game(hand=[("b1", "bombard")], effects=BOMBARD, creatures=[(3, 3, "bob", True)])  # commander, only answer
    check("burn_choice reserves our ONLY commander-answer (don't spend the last one)", _burn(g, "b1", "c0") == NEG)

    g = _game(hand=[("b1", "bombard"), ("b2", "bombard")], effects=BOMBARD, creatures=[(3, 3, "bob", True)])
    check("burn_choice kills the commander when a BACKUP answer is in hand", _burn(g, "b1", "c0") > 0)

    g = _game(hand=[("b1", "bombard")], effects=BOMBARD, creatures=[(5, 5, "bob", True), (2, 2, "bob")])
    check("commander un-killable by this spell -> reserve rule off, kills the 2/2", _burn(g, "b1", "c1") > 0)

    g = _game(hand=[("b1", "bombard")], effects=BOMBARD, creatures=[(3, 3, "bob", True), (2, 2, "bob")])
    check("only commander-answer is held even with another creature killable (reserve holds the whole spell)",
          _burn(g, "b1", "c1") == NEG)

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

    # MANA ROCKS / DORKS deployed before other creatures (ramp first).
    def _ramp_game():
        st = {
            "is_player": {("alice",), ("bob",)}, "life": {("alice", 20), ("bob", 20)},
            "active_player": {("alice",)}, "has_priority": {("alice",)}, "current_step": {("precombat_main",)},
            "in_hand": {("alice", x) for x in ("rock", "bear", "dork", "bolt")},
            "instance_of": {("rock", "mind_stone"), ("bear", "grizzly_bears"),
                            ("dork", "llanowar_elves"), ("bolt", "lightning_bolt")},
            "mana_ability": {("mind_stone", "{T}"), ("llanowar_elves", "{T}"),
                             ("chromatic_star", "{1}, {T}, Sacrifice ~")},
            "spell_type": {("rock", "artifact"), ("bear", "creature"), ("dork", "creature"), ("bolt", "instant")},
            "card_ability": {(s, "a0", "spell") for s in ("mind_stone", "grizzly_bears", "llanowar_elves")},
            "free_grant": {("alice", x) for x in ("rock", "bear", "dork", "bolt")},
            "mana_cost": {("rock", 2), ("bear", 2), ("dork", 1), ("bolt", 1)},
        }
        g = Game.from_state(st); p = SocietyOfControlPlayer(); p.bind(g, "alice")
        return g, p
    g, p = _ramp_game()
    def mc(c): return NS(kind="cast", card=NS(id=c), choices={})
    # is_mana_rock is the OBJECTIVE matcher the ramp line keys on (Do.SPELLS.matching(is_mana_rock)); it's true
    # of a tap-for-mana ROCK or DORK, false of a plain creature or a non-permanent — deck-independent.
    check("is_mana_rock True for a mana ROCK (Mind Stone artifact)", is_mana_rock(g, mc("rock")) is True)
    check("is_mana_rock True for a mana DORK (a creature that taps for mana)", is_mana_rock(g, mc("dork")) is True)
    check("is_mana_rock False for a plain creature", is_mana_rock(g, mc("bear")) is False)
    check("is_mana_rock False for a non-permanent spell", is_mana_rock(g, mc("bolt")) is False)
    check("curve_choice scores a matched mana source (above the floor=0.0 gate)", p.curve_choice(g, mc("rock")) > 0)
    check("choose_move deploys a mana source BEFORE the plain creature",
          getattr(getattr(p.choose_move(g), "card", None), "id", None) in ("rock", "dork"))

    print(f"\n{'ALL PASS' if not _fails else str(_fails) + ' FAILED'}")
    raise SystemExit(1 if _fails else 0)


if __name__ == "__main__":
    run()
