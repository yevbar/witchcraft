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


def _trigger_damage_checks() -> None:
    # §120 triggered direct damage (Flametongue Kavu-style): the ability fires, the driver picks the target.
    # 'src' (alice) has an upkeep trigger dealing 4 to a creature -> kills bob's 5/5? no (tough 5>4); kills
    # the 1/1 'small' if any_target (best_killable prefers a finishable threat).
    st = _base()
    st["has_trigger"] = {("ping", "src", "upkeep")}
    st["trigger_damage"] = {("ping", 4, "creature_any")}
    _run(st)
    check("triggered 4 dmg/creature kills a finishable creature (small dies)",
          ("small",) in st["graveyard"])

    # triggered damage to a player (kind face) -> opponent loses life, caster untouched (no self-burn).
    st = _base()
    st["has_trigger"] = {("zap", "src", "upkeep")}
    st["trigger_damage"] = {("zap", 3, "face")}
    _run(st)
    check("triggered face dmg hits opponent not caster (bob 17, alice 20)",
          ("bob", 17) in st["life"] and ("alice", 20) in st["life"])

    # any_target triggered damage with nothing killable -> face.
    st = _base()
    st["on_battlefield"] = {("mine",), ("big",), ("src",)}
    st["printed_control"] = {("alice", "mine"), ("alice", "src"), ("bob", "big")}
    st["has_trigger"] = {("bolt", "src", "upkeep")}
    st["trigger_damage"] = {("bolt", 1, "any_target")}
    _run(st)
    check("triggered any_target with nothing killable goes face (bob 19)", ("bob", 19) in st["life"])

    # the bridge routes a real ETB-burn trigger to trigger_damage, not a mistranslated player effect.
    import sim, card_corpus
    db = sim.load_db(); corpus = {c["name"]: c for c in card_corpus.load_cards()}
    found = None
    for name in corpus:
        try:
            f, _ = bridge.card_facts(name, "alice", "x", db, corpus)
        except Exception:
            continue
        if f.get("trigger_damage"):
            found = (name, sorted(f["trigger_damage"])); break
    check("a real triggered-damage card routes to trigger_damage", found is not None)


def _counter_checks() -> None:
    # §122 +1/+1 counter on a single 'target creature you control' -> the driver buffs its strongest own,
    # the counter persists (folds into the §613 layer P/T sum), not until-EOT.
    st = _base()
    st["has_trigger"] = {("grow", "src", "upkeep")}
    st["trigger_target"] = {("grow", "counter", "p1p1:1", "you_control")}
    _run(st)
    p = _powers(st)
    check("+1/+1 counter/you_control buffs strongest own (mine 2 -> 3)", p.get("mine") == 3)
    check("the counter is a real p1p1 counter on the creature",
          ("mine", "p1p1", 1) in st.get("counter", set()))

    # a -1/-1 counter is removal-flavored -> the strongest enemy; two of them shrink a 5/5 to 3/3.
    st = _base()
    st["has_trigger"] = {("wither", "src", "upkeep")}
    st["trigger_target"] = {("wither", "counter", "m1m1:2", "any")}
    _run(st)
    p = _powers(st)
    check("-1/-1 x2 counter/any shrinks strongest enemy (big 5 -> 3)", p.get("big") == 3)

    # a spell putting a +1/+1 counter on each creature you control (board scope).
    st = _base()
    st["spell_scope"] = {("anthemctr", "counter", "p1p1:1", "creatures_you_control")}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._run_spell_effects(st, "anthemctr", "alice")
    p = _powers(st)
    check("spell +1/+1 counter scope buffs all own (mine 2->3, src 1->2)",
          p.get("mine") == 3 and p.get("src") == 2)

    # the bridge routes a real 'put a +1/+1 counter on target creature' card to the targeting machinery.
    import sim, card_corpus
    db = sim.load_db(); corpus = {c["name"]: c for c in card_corpus.load_cards()}
    found = None
    for name in corpus:
        try:
            f, _ = bridge.card_facts(name, "alice", "x", db, corpus)
        except Exception:
            continue
        if any(v == "counter" for (_s, v, _p, _c) in f.get("spell_target", set()) | f.get("trigger_target", set())):
            found = name; break
    check("a real +1/+1-counter card routes to the targeting machinery", found is not None)


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

    # §120 direct damage. 'Shock' (2 to any target) cast by alice: kills bob's 1/1 'small' (lethal),
    # leaving the 5/5 — best_killable prefers a threat it can actually finish.
    st = _base()
    st["spell_damage"] = {("shock", 2, "any_target")}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._run_spell_effects(st, "shock", "alice")
    check("burn any_target kills a creature it can finish (small dies)",
          ("small",) in st["graveyard"] and ("big",) in st["on_battlefield"])

    # 'Lava Spike' (3 to target player) -> opponent loses 3 life, no creature touched.
    st = _base()
    st["spell_damage"] = {("spike", 3, "face")}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._run_spell_effects(st, "spike", "alice")
    check("burn face -> opponent loses life (bob 20 -> 17)", ("bob", 17) in st["life"])
    check("burn face hits the OPPONENT, not the caster (alice still 20)", ("alice", 20) in st["life"])

    # burn to a creature that can't be finished -> non-lethal, the creature survives.
    st = _base()
    st["spell_damage"] = {("zap", 1, "creature_opponent")}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._run_spell_effects(st, "zap", "alice")
    check("non-lethal burn leaves the creature alive (big survives 1 dmg)",
          ("big",) in st["on_battlefield"] and ("big",) not in st["graveyard"])

    # 'Flame Slash' (4 to target creature) -> destroys bob's 5/5? no (toughness 5 > 4) but kills nothing
    # it can't finish; with a 4-toughness target it WOULD. Verify lethality boundary on the 1/1.
    st = _base()
    st["spell_damage"] = {("slash", 5, "creature_any")}
    with contextlib.redirect_stdout(io.StringIO()):
        driver._run_spell_effects(st, "slash", "alice")
    check("burn creature_any with lethal n destroys the strongest enemy (big, 5 toughness)",
          ("big",) in st["graveyard"])

    # any_target with no killable creature -> go face. (Remove the 1/1 so 1 damage can't finish anything.)
    st = _base()
    st["on_battlefield"] = {("mine",), ("big",), ("src",)}
    st["printed_control"] = {("alice", "mine"), ("alice", "src"), ("bob", "big")}
    st["spell_damage"] = {("bolt", 1, "any_target")}     # 1 dmg can't kill the 2/2 or 5/5
    with contextlib.redirect_stdout(io.StringIO()):
        driver._run_spell_effects(st, "bolt", "alice")
    check("any_target with nothing killable goes face (bob 20 -> 19)", ("bob", 19) in st["life"])

    # a real burn spell routes deal_damage to spell_damage, not a dropped/mistranslated player effect.
    import sim as _sim, card_corpus as _cc
    _db = _sim.load_db(); _co = {c["name"]: c for c in _cc.load_cards()}
    burn = None
    for name in _co:
        try:
            f, _ = bridge.card_facts(name, "alice", "x", _db, _co)
        except Exception:
            continue
        if f.get("spell_damage"):
            burn = (name, sorted(f["spell_damage"])); break
    check("a real burn spell routes deal_damage to spell_damage", burn is not None)

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
    _trigger_damage_checks()
    _counter_checks()
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
