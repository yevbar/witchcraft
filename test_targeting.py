"""test_targeting.py — §115 / §601.2c SINGLE-TARGET triggered effects through the datalog engine.

The bridge emits trigger_target(ability, verb, payload, class) for a single 'target creature' clause; the
engine derives pending_target(ability, source, verb, payload, class, controller) when the ability fires; the
driver makes the §601.2c choice within the legal class and applies the verb. Removal/tap/bounce aim at the
strongest legal ENEMY; a buff/grant aims at the strongest OWN creature. Class constrains the legal set.

Run: python3 test_targeting.py   (needs datalog/cards.dl for the bridge checks)
"""

from __future__ import annotations

import contextlib
import io

import driver
import bridge_to_engine as bridge

CHECKS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _base() -> dict:
    """alice controls 'mine' (2/2); bob controls 'big' (5/5) and 'small' (1/1). 'src' is alice's source."""
    return {
        "current_step": {("upkeep",)}, "active_player": {("alice",)},
        "is_player": {("alice",), ("bob",)}, "life": {("alice", 20), ("bob", 20)},
        "on_battlefield": {("mine",), ("big",), ("small",), ("src",)},
        "printed_type": {("mine", "creature"), ("big", "creature"),
                         ("small", "creature"), ("src", "creature")},
        "printed_power": {("mine", 2), ("big", 5), ("small", 1), ("src", 1)},
        "printed_toughness": {("mine", 2), ("big", 5), ("small", 1), ("src", 1)},
        "printed_control": {("alice", "mine"), ("alice", "src"),
                            ("bob", "big"), ("bob", "small")},
        "counter": set(), "tapped": set(), "attacks": set(), "blocks": set(),
        "in_hand": set(), "graveyard": set(), "exile": set(),
    }


def _powers(state: dict) -> dict:
    return {c: int(n) for (c, n) in driver.run(state, ["power"])["power"]}


def _run(state: dict) -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        driver._apply_creature_effects(state)


def _driver_checks() -> None:
    # destroy 'target creature' (class any): removal -> strongest ENEMY (bob's 5/5 'big'), not alice's own.
    st = _base()
    st["has_trigger"] = {("zap", "src", "upkeep")}
    st["trigger_target"] = {("zap", "destroy", "-", "any")}
    _run(st)
    check("destroy/any -> strongest enemy (big) to graveyard",
          ("big",) in st["graveyard"] and ("big",) not in st["on_battlefield"])
    check("destroy/any spared alice's own creature (mine survives)", ("mine",) in st["on_battlefield"])

    # destroy class=opponent on a board where alice has the strongest creature: must still hit an opponent.
    st = _base()
    st["printed_power"] = {("mine", 9), ("big", 5), ("small", 1), ("src", 1)}
    st["printed_toughness"] = st["printed_power"]
    st["has_trigger"] = {("zap", "src", "upkeep")}
    st["trigger_target"] = {("zap", "destroy", "-", "opponent")}
    _run(st)
    check("destroy/opponent never targets own even if own is strongest",
          ("mine",) in st["on_battlefield"] and ("big",) in st["graveyard"])

    # +2/+2 buff (class you_control): beneficial -> strongest OWN creature ('mine' 2/2 -> 4/4), not enemy.
    st = _base()
    st["has_trigger"] = {("pump", "src", "upkeep")}
    st["trigger_target"] = {("pump", "modify_pt", "2/2", "you_control")}
    _run(st)
    p = _powers(st)
    check("buff/you_control -> strongest own creature pumped (mine 2 -> 4)", p.get("mine") == 4)
    check("buff/you_control left enemy untouched (big stays 5)", p.get("big") == 5)

    # a net-negative pump (-3/-3, class any) is removal-flavored -> aim at the strongest ENEMY.
    st = _base()
    st["has_trigger"] = {("weak", "src", "upkeep")}
    st["trigger_target"] = {("weak", "modify_pt", "-3/-3", "any")}
    _run(st)
    p = _powers(st)
    check("shrink (-3/-3)/any aims at strongest enemy (big 5 -> 2)", p.get("big") == 2)
    check("shrink left own creature alone (mine stays 2)", p.get("mine") == 2)

    # tap 'target creature' (class any): tempo removal -> strongest enemy gets tapped.
    st = _base()
    st["has_trigger"] = {("hold", "src", "upkeep")}
    st["trigger_target"] = {("hold", "tap", "-", "any")}
    _run(st)
    check("tap/any taps the strongest enemy (big)", ("big",) in st["tapped"])

    # return_to_hand (bounce) -> strongest enemy returns to ITS controller's (bob's) hand.
    st = _base()
    st["has_trigger"] = {("bnc", "src", "upkeep")}
    st["trigger_target"] = {("bnc", "return_to_hand", "-", "any")}
    _run(st)
    check("bounce/any returns strongest enemy to its owner's hand",
          ("bob", "big") in st["in_hand"] and ("big",) not in st["on_battlefield"])

    # grant keyword (class you_control) -> strongest own creature gains the keyword until EOT.
    st = _base()
    st["has_trigger"] = {("fly", "src", "upkeep")}
    st["trigger_target"] = {("fly", "grant", "flying", "you_control")}
    _run(st)
    has_kw = driver.run(st, ["has_keyword"])["has_keyword"]
    check("grant/you_control -> strongest own creature gains the keyword",
          ("mine", "flying") in has_kw and ("big", "flying") not in has_kw)

    # no legal target (opponent class, but opponent controls nothing) -> abstain, no crash, no effect.
    st = _base()
    st["on_battlefield"] = {("mine",), ("src",)}
    st["printed_control"] = {("alice", "mine"), ("alice", "src")}
    st["has_trigger"] = {("zap", "src", "upkeep")}
    st["trigger_target"] = {("zap", "destroy", "-", "opponent")}
    _run(st)
    check("no legal target -> nothing destroyed", ("mine",) in st["on_battlefield"]
          and not st["graveyard"])

    # indestructible target is chosen but not destroyed (§702.12b).
    st = _base()
    st["has_trigger"] = {("zap", "src", "upkeep")}
    st["trigger_target"] = {("zap", "destroy", "-", "any")}
    st["printed_keyword"] = {("big", "indestructible")}
    _run(st)
    check("indestructible target survives a destroy", ("big",) in st["on_battlefield"])


def _spell_checks() -> None:
    # §608 instant/sorcery single-target effects resolve through _run_spell_effects via spell_target.
    # 'Murder' (destroy target creature) cast by alice -> kills bob's strongest (big), spares her own.
    st = _base()
    st["spell_target"] = {("murder", "destroy", "-", "any")}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._run_spell_effects(st, "murder", "alice")
    check("spell destroy/any kills strongest enemy (big -> graveyard)",
          ("big",) in st["graveyard"] and ("mine",) in st["on_battlefield"])

    # 'Giant Growth' (+3/+3 to target creature you control) -> alice's strongest own creature.
    st = _base()
    st["spell_target"] = {("growth", "modify_pt", "3/3", "you_control")}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._run_spell_effects(st, "growth", "alice")
    check("spell +3/+3/you_control pumps own creature (mine 2 -> 5)", _powers(st).get("mine") == 5)

    # 'Unsummon' (return target creature to owner's hand) -> bounces strongest enemy to bob's hand.
    st = _base()
    st["spell_target"] = {("unsummon", "return_to_hand", "-", "any")}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._run_spell_effects(st, "unsummon", "alice")
    check("spell bounce returns strongest enemy to its owner's hand",
          ("bob", "big") in st["in_hand"] and ("big",) not in st["on_battlefield"])

    # 'Overrun' (creatures you control get +3/+3) -> pumps ALL of alice's creatures, none of bob's.
    st = _base()
    st["spell_scope"] = {("overrun", "modify_pt", "3/3", "creatures_you_control")}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._run_spell_effects(st, "overrun", "alice")
    p = _powers(st)
    check("spell scope creatures_you_control pumps all own (mine 2->5, src 1->4)",
          p.get("mine") == 5 and p.get("src") == 4)
    check("spell scope creatures_you_control leaves enemies (big stays 5, small 1)",
          p.get("big") == 5 and p.get("small") == 1)

    # 'Wrath of God' (destroy all creatures) -> every creature to the graveyard.
    st = _base()
    st["spell_scope"] = {("wrath", "destroy", "-", "all_creatures")}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._run_spell_effects(st, "wrath", "alice")
    check("spell scope all_creatures destroys the whole board",
          all((c,) in st["graveyard"] for c in ("mine", "big", "small", "src"))
          and not st["on_battlefield"])

    # a board wipe spares indestructible creatures (§702.12b).
    st = _base()
    st["printed_keyword"] = {("big", "indestructible")}
    st["spell_scope"] = {("wrath", "destroy", "-", "all_creatures")}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._run_spell_effects(st, "wrath", "alice")
    check("board wipe spares the indestructible creature (big survives)",
          ("big",) in st["on_battlefield"] and ("mine",) in st["graveyard"])

    # the bridge routes a real removal spell's destroy clause to spell_target, not a dropped effect.
    import sim, card_corpus
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}
    found = None
    for name in corpus:
        try:
            f, _ = bridge.card_facts(name, "alice", "x", db, corpus)
        except Exception:
            continue
        if any(v in ("destroy", "modify_pt", "return_to_hand") for (_s, v, _p, _c) in f.get("spell_target", set())):
            found = (name, sorted(f["spell_target"]))
            break
    check("a real instant/sorcery routes a single-target verb to spell_target", found is not None)


def _bridge_checks() -> None:
    import sim, card_corpus
    db = sim.load_db()
    corpus = {c["name"]: c for c in card_corpus.load_cards()}

    # _target_class maps clean single-target slugs to a class; restricted/named targets abstain (None).
    check("target_creature -> any", bridge._target_class("target_creature") == "any")
    check("target_creature_you_control -> you_control",
          bridge._target_class("target_creature_you_control") == "you_control")
    check("target_creature_an_opponent_controls -> opponent",
          bridge._target_class("target_creature_an_opponent_controls") == "opponent")
    check("restricted target abstains (no class)",
          bridge._target_class("target_creature_with_power_3_or_greater") is None)


def run() -> None:
    _driver_checks()
    _spell_checks()
    _bridge_checks()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
