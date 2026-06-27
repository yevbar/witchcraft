"""Unit + end-to-end checks for effect_handlers/fight.py — §701.12 fight.

ENCODE half: each (tgt, extra) operand pair -> a side spec ('A|B') or None (abstain).
APPLY half: drive driver._apply_effects with a 'fight' pending row over a constructed board and confirm
each creature deals its (snapshotted) power to the other, with the §704 destroy path / indestructible /
toughness all respected (reused from driver._apply_damage).
"""

from __future__ import annotations

import io
import contextlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import effect_handlers
import effect_handlers.fight as F
import driver as D

CHECKS: list = []


def check(name: str, ok: bool) -> None:
    CHECKS.append((name, bool(ok)))


def _encode_checks() -> None:
    enc = F.encode_fight
    # the classic fight: 'target creature you control fights target creature you don't control'
    check("own vs enemy -> own|enemy",
          enc("fight", "-", "target_creature_you_control", "target_creature_you_don_t_control") == ("fight", 0, "own|enemy"))
    # 'it'/'self' -> src; opponent-controls -> enemy
    check("it vs opp-controls -> src|enemy",
          enc("fight", "-", "it", "target_creature_an_opponent_controls") == ("fight", 0, "src|enemy"))
    check("self vs another -> src|another",
          enc("fight", "-", "self", "another_target_creature") == ("fight", 0, "src|another"))
    # 'target creature fights another target creature' (Blood Feud) -> any|another
    check("any vs another -> any|another",
          enc("fight", "-", "target_creature", "another_target_creature") == ("fight", 0, "any|another"))
    # enchanted/equipped host
    check("enchanted host -> host|enemy",
          enc("fight", "-", "enchanted_creature", "target_creature_you_don_t_control") == ("fight", 0, "host|enemy"))

    # ABSTAIN: 'up to one' (a choice to fight zero)
    check("up to one abstains",
          enc("fight", "-", "self", "up_to_one_target_creature_you_don_t_control") is None)
    # ABSTAIN: 'each other' (mass/reciprocal)
    check("each other abstains",
          enc("fight", "-", "those_creatures", "each_other") is None)
    # ABSTAIN: 'that creature' (untracked back-reference)
    check("that_creature side A abstains",
          enc("fight", "-", "that_creature", "target_creature_an_opponent_controls") is None)
    check("that_creature side B abstains",
          enc("fight", "-", "self", "that_creature") is None)
    # ABSTAIN: subtype / color / counter-classed operands
    check("green-classed abstains",
          enc("fight", "-", "it", "target_green_creature_an_opponent_controls") is None)
    check("counter-classed side A abstains",
          enc("fight", "-", "target_creature_you_control_with_a_1_1_counter_on_it", "target_creature_an_opponent_controls") is None)
    # ABSTAIN: 'defending player controls' (combat-context, not a plain enemy set)
    check("defending-player side B abstains",
          enc("fight", "-", "it", "target_creature_defending_player_controls") is None)


def _board(specs: dict) -> dict:
    """specs: id -> (controller, power, toughness, *flags). flag 'indestructible' marks cant_be_destroyed.
    Returns a minimal driver state the engine can derive creature/power/eff_toughness/controls from."""
    state = {
        "is_player": {("alice",), ("bob",)}, "life": {("alice", 20), ("bob", 20)},
        "on_battlefield": set(), "printed_type": set(), "printed_power": set(),
        "printed_toughness": set(), "printed_control": set(),
        "counter": set(), "tapped": set(), "graveyard": set(),
        "has_keyword": set(),
    }
    for cid, (ctrl, p, t, *flags) in specs.items():
        state["on_battlefield"].add((cid,))
        state["printed_type"].add((cid, "creature"))
        state["printed_power"].add((cid, p))
        state["printed_toughness"].add((cid, t))
        state["printed_control"].add((ctrl, cid))
        if "indestructible" in flags:
            state.setdefault("has_keyword", set()).add((cid, "indestructible"))
    return state


def _fight(state: dict, payload: str, ctrl: str = "alice", src: str = "spell") -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        D._apply_effects(state, {("fightA", "fight", 0, payload, src, ctrl)})


def _gy(state) -> set:
    return {c for (c,) in state.get("graveyard", set())}


def _bf(state) -> set:
    return {c for (c,) in state.get("on_battlefield", set())}


def _apply_checks() -> None:
    effect_handlers.load()

    # (1) classic lethal fight: alice's 3/3 'mine' fights bob's 2/2 'foe'. Each deals power: foe takes 3 (>=2,
    #     dies); mine takes 2 (>=3? no -> survives). Greedy picks mine (own) vs foe (strongest enemy).
    st = _board({"mine": ("alice", 3, 3), "foe": ("bob", 2, 2)})
    _fight(st, "own|enemy")
    check("classic: foe (2/2) dies to 3 power", "foe" in _gy(st) and "foe" not in _bf(st))
    check("classic: mine (3/3) survives 2 power", "mine" in _bf(st) and "mine" not in _gy(st))

    # (2) MUTUAL lethal (snapshot simultaneity): 3/3 vs 3/3 — each deals 3, both die. If we applied damage
    #     sequentially WITHOUT snapshotting, the first death would still let the dead body deal its 3 (we read
    #     power up-front), so both must land in the graveyard.
    st = _board({"mine": ("alice", 3, 3), "foe": ("bob", 3, 3)})
    _fight(st, "own|enemy")
    check("mutual lethal: mine dies", "mine" in _gy(st))
    check("mutual lethal: foe dies", "foe" in _gy(st))

    # (3) INDESTRUCTIBLE survives lethal fight damage. Brash-Taunter-style: a 1/1 indestructible fights a 5/5;
    #     the taunter takes 5 (would be lethal) but can't be destroyed; the 5/5 takes 1 (non-lethal) -> both live.
    st = _board({"taunter": ("alice", 1, 1, "indestructible"), "big": ("bob", 5, 5)})
    _fight(st, "own|enemy")
    check("indestructible: taunter survives lethal fight damage", "taunter" in _bf(st) and "taunter" not in _gy(st))
    check("indestructible: big (5/5) survives 1 power", "big" in _bf(st))

    # (4) src-side fight (activated/triggered 'this creature fights ...'): src IS the creature, not a spell.
    #     A 4/4 source fights bob's 4/4 -> both die.
    st = _board({"hydra": ("alice", 4, 4), "rival": ("bob", 4, 4)})
    _fight(st, "src|enemy", src="hydra")
    check("src fight: source hydra dies", "hydra" in _gy(st))
    check("src fight: rival dies", "rival" in _gy(st))

    # (5) src spec for a SPELL (src not on battlefield) degrades to 'own' — alice's 6/6 fights bob's 2/2.
    st = _board({"beast": ("alice", 6, 6), "weenie": ("bob", 2, 2)})
    _fight(st, "src|enemy", src="spell")   # 'spell' is not a battlefield creature -> falls back to own
    check("spell 'src' -> own: beast survives", "beast" in _bf(st))
    check("spell 'src' -> own: weenie dies", "weenie" in _gy(st))

    # (6) no legal side-B creature -> no fight, nothing dies.
    st = _board({"lonely": ("alice", 3, 3)})
    _fight(st, "own|enemy")
    check("no enemy: lonely survives (no fight)", "lonely" in _bf(st))

    # (7) zero-power participant deals no damage: alice's 0/1 fights bob's 1/1. foe takes 0 (lives); mine takes
    #     1 (>=1 -> dies). Confirms a 0-power creature is harmless but still takes damage.
    st = _board({"wall": ("alice", 0, 1), "pinger": ("bob", 1, 1)})
    _fight(st, "own|enemy")
    check("zero power: enemy survives 0 damage", "pinger" in _bf(st))
    check("zero power: own 0/1 dies to 1 power", "wall" in _gy(st))


def _real_card_checks() -> None:
    """Faithful-or-abstain on REAL corpus cards via card_facts: the determinable fights emit a fight tuple;
    the choice/back-reference fights abstain (stay in the drop list)."""
    import sim, card_corpus, bridge_to_engine as bridge
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}

    def fight_rows(name):
        f, dr = bridge.card_facts(name, "alice", "x", db, corpus)
        rows = [r for rel in ("spell_effect", "trigger_effect") for r in f.get(rel, set())
                if len(r) >= 2 and r[1] == "fight"]
        rows += [r for r in f.get("activated_ability", set()) if len(r) >= 5 and r[4] == "fight"]
        dropped = sum(1 for d in dr if d == ("effect", "fight"))
        return rows, dropped

    rows, _ = fight_rows("Prey Upon")
    check("Prey Upon emits own|enemy", any(r[1] == "fight" and r[3] == "own|enemy" for r in rows if len(r) == 4))
    rows, _ = fight_rows("Ulvenwald Tracker")
    check("Ulvenwald Tracker (activated) emits own|another",
          any(len(r) == 7 and r[4] == "fight" and r[6] == "own|another" for r in rows))
    rows, _ = fight_rows("Nightfall Predator")
    check("Nightfall Predator (activated) emits src|any",
          any(len(r) == 7 and r[4] == "fight" and r[6] == "src|any" for r in rows))
    # ABSTAIN: an 'up to one' fight stays dropped, never emitted.
    rows, dropped = fight_rows("Thorn Mammoth")
    check("Thorn Mammoth (up to one) emits no fight", not rows)
    # ABSTAIN: The Tarrasque's 'defending player controls' fight stays dropped.
    rows, dropped = fight_rows("The Tarrasque")
    check("The Tarrasque (defending-player) abstains", not rows and dropped >= 1)


def run() -> None:
    _encode_checks()
    _apply_checks()
    _real_card_checks()
    failed = [n for n, ok in CHECKS if not ok]
    for n, ok in CHECKS:
        print(("PASS " if ok else "FAIL ") + n)
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} passed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    run()
