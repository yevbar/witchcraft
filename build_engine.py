"""Build datalog/engine.dl (full, with tests) and datalog/engine_rules.dl (rules
only, for the driver) deterministically from explicit structures.

Integrates layer 7 (§613.4), combat (§510), keywords (§702), SBAs (§704), turn
structure (§500), casting (§601/§117.1a), mana (§118.3), targeting (§115), stack
(§608). Derivations only — applying consequences/looping is the driver's job.
`on_battlefield` is an explicit zone the driver mutates; `advance_to` is the next
step the driver moves to.
"""

from __future__ import annotations

from pathlib import Path

from dlgen import Program
from rules_parser import split
from transpile import transpile_rule
from build_casting import extract as _casting_extract
from build_zones import extract as _zones_extract
from build_lookback import extract as _lookback_extract
from build_supertypes import extract as _supertypes_extract
from build_keyword_ability_index import roster as _keyword_ability_roster


def _casting_resolves() -> list:
    """The §3 resolves_to facts interpreted from rules.txt — the engine depends on these
    instead of hardcoding which card types become permanents."""
    return _casting_extract()[1]


def _casting_perms() -> list:
    """The §3 cast_permission(type, action, speed) facts — the engine depends on these for
    casting timing instead of hardcoding the instant-vs-sorcery split."""
    return _casting_extract()[0]


def _supertype_rule_facts() -> list[str]:
    """The §205.4 supertype_rule facts interpreted from rules.txt — the engine depends on these
    for the legendary classification and the legendary-spell casting restriction (§205.4e)."""
    return list(dict.fromkeys(
        f'supertype_rule("{sup}", "{subj}", "{rule}")' for _n, sup, subj, rule in _supertypes_extract()))


def _lookback_events() -> list[str]:
    """The §603.10 'look back in time' event phrases interpreted from rules.txt — the trigger
    layer depends on these to mark which of its events use last-known information."""
    return list(dict.fromkeys(clause for _n, clause in _lookback_extract()))


# Maps each engine trigger-event key to the §603.10 phrase it represents. The look-back STATUS
# is NOT asserted here — it's derived by joining this bridge against the interpreted
# looks_back_in_time facts, so if the rules stop classifying a phrase as look-back, the
# corresponding event keys drop out automatically. Phrases must match build_lookback's output.
_LOOKBACK_BRIDGE = [
    ("dies_self", "permanent leaves the battlefield"),
    ("dies_other", "permanent leaves the battlefield"),
    ("sacrificed_self", "player sacrifices a permanent"),
    ("sacrificed_other", "player sacrifices a permanent"),
    ("phased_out_self", "permanent phases out"),
    ("countered_self", "spell is countered"),
    ("player_loses_game", "player loses the game"),
]


def _zone_restriction_facts() -> list[str]:
    """The §4 cant_enter/cant_leave atoms interpreted from rules.txt (deduped) — the engine
    depends on these to block illegal zone moves (e.g. a conspiracy can't leave command)."""
    return list(dict.fromkeys(
        f'{"cant_enter" if d == "enter" else "cant_leave"}("{t}", "{z}")'
        for _n, t, z, d in _zones_extract()))

# §702 prohibition/evasion rules the engine DEPENDS ON, pulled from the English
# text by the transpiler (not hand-written here). Document order is deterministic.
TRANSPILED_702 = {"702.3b", "702.9b", "702.12b", "702.111b"}


def _transpiled(group: str, wanted: set) -> list[tuple[str, str]]:
    """Transpile the requested subrules of a group straight from rules.txt."""
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    out = []
    for s in doc.sections:
        for g in s.groups:
            if g.number == group:
                for r in g.rules:
                    for sr in [r] + r.subrules:
                        if sr.number in wanted:
                            o = transpile_rule(sr.number, sr.text)
                            if o:
                                out.append((sr.number, o.datalog))
    return out

# The turn's steps come from §500/§501/§506/§512 via the lark extractor — the
# engine's step order is rules-derived, not a hand-maintained list.
from build_turn_structure import flat_steps  # noqa: E402
from build_turn_actions import main_phases as _main_phases  # noqa: E402
from build_ending import thresholds as _loss_thresholds  # noqa: E402

STEPS = flat_steps()

INPUTS = [
    ("on_battlefield", [("c", "symbol")]),          # the zone the driver mutates
    ("printed_type", [("c", "symbol"), ("t", "symbol")]),       # §613 layer-system BASE characteristics
    ("printed_control", [("p", "symbol"), ("c", "symbol")]),
    ("printed_color", [("c", "symbol"), ("col", "symbol")]),
    ("printed_subtype", [("c", "symbol"), ("st", "symbol")]),
    # OVERRIDE effects carry a timestamp (ts); §613.7 — within a layer the LATEST applies.
    ("eff_copy", [("e", "symbol"), ("c", "symbol"), ("original", "symbol"), ("ts", "number")]),       # layer 1 copy
    ("eff_text_change", [("e", "symbol"), ("c", "symbol"), ("frm", "symbol"), ("to", "symbol")]),  # layer 3 text change
    ("eff_gain_control", [("e", "symbol"), ("p", "symbol"), ("c", "symbol"), ("ts", "number")]),      # layer 2 control
    ("eff_add_type", [("e", "symbol"), ("c", "symbol"), ("t", "symbol")]),          # layer 4 type change (commutes)
    ("eff_remove_type", [("e", "symbol"), ("c", "symbol"), ("t", "symbol")]),
    ("eff_set_color", [("e", "symbol"), ("c", "symbol"), ("col", "symbol"), ("ts", "number")]),       # layer 5 color
    ("is_player", [("p", "symbol")]),
    ("printed_power", [("c", "symbol"), ("n", "number")]),
    ("printed_toughness", [("c", "symbol"), ("n", "number")]),
    ("counter", [("c", "symbol"), ("kind", "symbol"), ("n", "number")]),
    ("mod_power", [("c", "symbol"), ("dp", "number")]),         # §613.4 layer 7c P/T modifier
    ("mod_toughness", [("c", "symbol"), ("dt", "number")]),
    ("printed_keyword", [("c", "symbol"), ("kw", "symbol")]),   # §613 layer-system BASE characteristics
    ("eff_grant_keyword", [("e", "symbol"), ("c", "symbol"), ("kw", "symbol")]),    # layer 6 grant
    ("eff_remove_keyword", [("e", "symbol"), ("c", "symbol"), ("kw", "symbol")]),   # layer 6 remove
    ("eff_set_power", [("e", "symbol"), ("c", "symbol"), ("n", "number"), ("ts", "number")]),    # layer 7b set
    ("eff_set_toughness", [("e", "symbol"), ("c", "symbol"), ("n", "number"), ("ts", "number")]),
    ("eff_switch_pt", [("e", "symbol"), ("c", "symbol")]),                          # layer 7d switch (parity)
    ("life", [("p", "symbol"), ("n", "number")]),
    ("poison", [("p", "symbol"), ("n", "number")]),
    ("attacks", [("a", "symbol"), ("d", "symbol")]),
    ("blocks", [("b", "symbol"), ("a", "symbol")]),
    ("current_step", [("s", "symbol")]),
    ("active_player", [("p", "symbol")]),
    ("has_priority", [("p", "symbol")]),
    ("in_hand", [("p", "symbol"), ("spell", "symbol")]),
    ("spell_type", [("spell", "symbol"), ("t", "symbol")]),
    ("mana_cost", [("spell", "symbol"), ("n", "number")]),
    ("mana_available", [("p", "symbol"), ("n", "number")]),
    ("on_stack", [("o", "symbol"), ("pos", "number")]),
    ("all_passed", [("marker", "symbol")]),
    ("targets", [("spell", "symbol"), ("target", "symbol")]),
    ("tapped", [("c", "symbol")]),                  # for the untap turn-based action
    ("has_trigger", [("ability", "symbol"), ("source", "symbol"), ("event", "symbol")]),   # §603 triggered abilities
    ("trigger_effect", [("ability", "symbol"), ("effect", "symbol"), ("amount", "number"), ("target", "symbol")]),
    # §603.10 look-back events the engine doesn't otherwise derive (driver/scenario supplies them).
    ("has_supertype", [("o", "symbol"), ("sup", "symbol")]),      # §205.4 supertypes (legendary etc.)
    ("sacrificed", [("o", "symbol")]),                            # §603.10a a permanent was sacrificed
    ("phased_out", [("o", "symbol")]),                            # §603.10b a permanent phased out
    ("countered", [("o", "symbol")]),                             # §603.10e a spell was countered
    # §614/§615 REPLACEMENT effects — cards reference these constantly; the engine provides the framework.
    ("repl_prevent_damage", [("e", "symbol"), ("src", "symbol"), ("tgt", "symbol")]),       # §615 prevent
    ("repl_enters_tapped", [("e", "symbol"), ("c", "symbol")]),                             # §614 "enters tapped"
    ("repl_enters_with_counter", [("e", "symbol"), ("c", "symbol"), ("kind", "symbol"), ("n", "number")]),
    # §115 targeting depth
    ("spell_color", [("s", "symbol"), ("col", "symbol")]),
    ("protection_from", [("t", "symbol"), ("col", "symbol")]),
    ("cant_be_targeted", [("t", "symbol")]),
    # §601/§700 choices/modes
    ("spell_mode", [("s", "symbol"), ("mode", "symbol")]),          # a mode the spell offers
    ("chose_mode", [("s", "symbol"), ("mode", "symbol")]),          # the mode the controller chose
    # §611 duration: a continuous effect that lasts only until end of turn
    ("until_eot", [("e", "symbol")]),
    ("is_keyword", [("kw", "symbol")]),                            # §122.1b which counter kinds are keyword counters
]

EXPECT_DECLS = [
    ("expect_dies", [("c", "symbol")]), ("expect_survives", [("c", "symbol")]),
    ("expect_loses", [("p", "symbol")]), ("expect_power", [("c", "symbol"), ("n", "number")]),
    ("expect_can_cast", [("p", "symbol"), ("s", "symbol")]),
    ("expect_no_cast", [("p", "symbol"), ("s", "symbol")]), ("expect_enters", [("o", "symbol")]),
    ("expect_illegal_block", [("b", "symbol"), ("a", "symbol")]),
    ("expect_no_loss", [("p", "symbol")]),
    ("expect_fires", [("a", "symbol"), ("s", "symbol")]),
    ("expect_controls", [("p", "symbol"), ("c", "symbol")]),
    ("expect_type", [("c", "symbol"), ("t", "symbol")]),
    ("expect_color", [("c", "symbol"), ("col", "symbol")]),
    ("expect_keyword", [("c", "symbol"), ("kw", "symbol")]),
    ("expect_subtype", [("c", "symbol"), ("st", "symbol")]),
    ("expect_etb_tapped", [("c", "symbol")]),
    ("expect_etb_counter", [("c", "symbol"), ("kind", "symbol"), ("n", "number")]),
    ("expect_fizzle", [("s", "symbol")]), ("expect_mode", [("s", "symbol"), ("m", "symbol")]),
    ("expect_ends_cleanup", [("e", "symbol")]),
    ("expect_lookback", [("event_key", "symbol")]),
]
CHECKS = [
    ("dies", "expect_dies(C)", "miss", "dies(C)"),
    ("survives", "expect_survives(C)", "hit", "dies(C)"),
    ("loses", "expect_loses(P)", "miss", "loses_game(P)"),
    ("power", "expect_power(C, N)", "miss", "power(C, N)", "C", '"-"'),
    ("can_cast", "expect_can_cast(P, S)", "miss", "can_cast(P, S)"),
    ("no_cast", "expect_no_cast(P, S)", "hit", "can_cast(P, S)"),
    ("enters", "expect_enters(O)", "miss", "enters_battlefield(O)"),
    ("illegal_block", "expect_illegal_block(B, A)", "miss", "illegal_block(B, A)"),
    ("no_loss", "expect_no_loss(P)", "hit", "loses_game(P)"),
    ("fires", "expect_fires(A, S)", "miss", "fires(A, S)"),
    ("controls", "expect_controls(P, C)", "miss", "controls(P, C)"),
    ("ctype", "expect_type(C, T)", "miss", "has_type(C, T)"),
    ("color", "expect_color(C, Col)", "miss", "color(C, Col)"),
    ("ckw", "expect_keyword(C, K)", "miss", "has_keyword(C, K)"),
    # every keyword the engine grants must be a defined §702 ability (interpreted roster)
    ("unknown_keyword", "has_keyword(_, K)", "miss", "keyword_ability(K)", "K", '"-"'),
    ("csub", "expect_subtype(C, ST)", "miss", "subtype(C, ST)"),
    ("etbtap", "expect_etb_tapped(C)", "miss", "enters_tapped(C)"),
    ("etbctr", "expect_etb_counter(C, K, N)", "miss", "enters_with_counter(C, K, N)", "C", "K"),
    ("fizzle", "expect_fizzle(S)", "miss", "fizzles(S)"),
    ("mode", "expect_mode(S, M)", "miss", "active_mode(S, M)"),
    ("ends", "expect_ends_cleanup(E)", "miss", "ends_at_cleanup(E)"),
    ("lookback", "expect_lookback(K)", "miss", "lookback_trigger(K)"),
]

SCENARIOS = [
    'current_step("combat_damage")', 'active_player("alice")', 'has_priority("alice")', 'all_passed("yes")',
    'on_battlefield("hero")', 'printed_type("hero", "creature")', 'printed_power("hero", 2)', 'printed_toughness("hero", 2)', 'counter("hero", "p1p1", 1)', 'printed_control("alice", "hero")',
    'expect_power("hero", 3)',
    'on_battlefield("blocker")', 'printed_type("blocker", "creature")', 'printed_power("blocker", 3)', 'printed_toughness("blocker", 3)', 'printed_control("bob", "blocker")',
    'attacks("hero", "bob")', 'blocks("blocker", "hero")', 'expect_dies("hero")', 'expect_dies("blocker")',
    'on_battlefield("rot")', 'printed_type("rot", "creature")', 'printed_power("rot", 4)', 'printed_toughness("rot", 4)', 'printed_keyword("rot", "wither")', 'printed_control("alice", "rot")',
    'on_battlefield("ox")', 'printed_type("ox", "creature")', 'printed_power("ox", 0)', 'printed_toughness("ox", 4)', 'printed_control("bob", "ox")',
    'attacks("rot", "bob")', 'blocks("ox", "rot")', 'expect_dies("ox")', 'expect_survives("rot")',
    'on_battlefield("plague")', 'printed_type("plague", "creature")', 'printed_power("plague", 2)', 'printed_toughness("plague", 2)', 'printed_keyword("plague", "infect")', 'printed_control("alice", "plague")',
    'is_player("carol")', 'poison("carol", 8)', 'attacks("plague", "carol")', 'expect_loses("carol")',
    'on_battlefield("raider")', 'printed_type("raider", "creature")', 'printed_power("raider", 5)', 'printed_toughness("raider", 5)', 'printed_control("alice", "raider")',
    'is_player("ed")', 'life("ed", 3)', 'attacks("raider", "ed")', 'expect_loses("ed")',
    'on_battlefield("pumped")', 'printed_type("pumped", "creature")', 'printed_power("pumped", 2)', 'printed_toughness("pumped", 2)', 'mod_power("pumped", 2)', 'mod_toughness("pumped", 2)', 'printed_control("alice", "pumped")',
    'expect_power("pumped", 4)',
    'on_battlefield("troll")', 'printed_type("troll", "creature")', 'printed_power("troll", 3)', 'printed_toughness("troll", 3)', 'printed_keyword("troll", "hexproof")', 'printed_control("bob", "troll")',
    'in_hand("alice", "bolt")', 'spell_type("bolt", "instant")', 'mana_cost("bolt", 1)', 'targets("bolt", "blocker")',
    'in_hand("alice", "wrath")', 'spell_type("wrath", "sorcery")', 'mana_cost("wrath", 2)',
    'in_hand("alice", "snipe")', 'spell_type("snipe", "instant")', 'mana_cost("snipe", 1)', 'targets("snipe", "troll")',
    'mana_available("alice", 3)',
    'expect_can_cast("alice", "bolt")', 'expect_no_cast("alice", "wrath")', 'expect_no_cast("alice", "snipe")',
    # §205.4e — alice's legendary instant "decree" can't be cast: she controls no legendary creature/planeswalker.
    'in_hand("alice", "decree")', 'has_supertype("decree", "legendary")', 'spell_type("decree", "instant")', 'mana_cost("decree", 1)',
    'expect_no_cast("alice", "decree")',
    # §205.4e satisfied — bob controls a legendary planeswalker, so his legendary instant "edict" is castable.
    'has_priority("bob")', 'mana_available("bob", 2)',
    'in_hand("bob", "edict")', 'has_supertype("edict", "legendary")', 'spell_type("edict", "instant")', 'mana_cost("edict", 1)',
    'printed_control("bob", "jace_leg")', 'printed_type("jace_leg", "planeswalker")', 'has_supertype("jace_leg", "legendary")',
    'expect_can_cast("bob", "edict")',
    'on_stack("elemental", 9)', 'spell_type("elemental", "creature")', 'expect_enters("elemental")',
    # §702.12b via transpiled cant_be_destroyed — golem takes lethal damage but survives.
    'on_battlefield("golem")', 'printed_type("golem", "creature")', 'printed_power("golem", 1)', 'printed_toughness("golem", 2)', 'printed_keyword("golem", "indestructible")', 'printed_control("bob", "golem")',
    'on_battlefield("striker")', 'printed_type("striker", "creature")', 'printed_power("striker", 3)', 'printed_toughness("striker", 3)', 'printed_control("alice", "striker")',
    'attacks("striker", "carol")', 'blocks("golem", "striker")', 'expect_survives("golem")',
    # §702.9b via transpiled illegal_block — a ground creature can't legally block a flyer.
    'on_battlefield("eagle")', 'printed_type("eagle", "creature")', 'printed_power("eagle", 2)', 'printed_toughness("eagle", 2)', 'printed_keyword("eagle", "flying")', 'printed_control("alice", "eagle")',
    'on_battlefield("grunt")', 'printed_type("grunt", "creature")', 'printed_power("grunt", 1)', 'printed_toughness("grunt", 5)', 'printed_control("bob", "grunt")',
    'attacks("eagle", "carol")', 'blocks("grunt", "eagle")', 'expect_illegal_block("grunt", "eagle")',
    # §702.9b ENFORCED — falcon (flyer) blocked only by a ground creature is unblocked, kills frank.
    'is_player("frank")', 'life("frank", 2)',
    'on_battlefield("falcon")', 'printed_type("falcon", "creature")', 'printed_power("falcon", 3)', 'printed_toughness("falcon", 3)', 'printed_keyword("falcon", "flying")', 'printed_control("alice", "falcon")',
    'on_battlefield("peon")', 'printed_type("peon", "creature")', 'printed_power("peon", 1)', 'printed_toughness("peon", 5)', 'printed_control("bob", "peon")',
    'attacks("falcon", "frank")', 'blocks("peon", "falcon")', 'expect_loses("frank")',
    # §702.3b ENFORCED — a 6/6 defender forced into combat deals no damage; greg survives.
    'is_player("greg")', 'life("greg", 3)',
    'on_battlefield("rampart")', 'printed_type("rampart", "creature")', 'printed_power("rampart", 6)', 'printed_toughness("rampart", 6)', 'printed_keyword("rampart", "defender")', 'printed_control("alice", "rampart")',
    'attacks("rampart", "greg")', 'expect_no_loss("greg")', 'tapped("rampart")',
    # §603 — hero dies in combat (death trigger fires) AND deals combat damage to the blocker (creature).
    'has_trigger("t1", "hero", "dies_self")', 'trigger_effect("t1", "lose_life", 2, "each_opponent")',
    'has_trigger("t2", "hero", "combat_damage_to_creature")', 'trigger_effect("t2", "draw", 1, "controller")',
    'expect_fires("t1", "hero")', 'expect_fires("t2", "hero")',
    # §603.10 look-back events — an altar with a "when sacrificed" trigger fires when sacrificed,
    # and that event key is marked look-back (it bridges to "player sacrifices a permanent").
    'has_trigger("t3", "altar", "sacrificed_self")', 'trigger_effect("t3", "draw", 1, "controller")',
    'controls("alice", "altar")', 'sacrificed("altar")', 'phased_out("mist")', 'countered("bolt2")',
    'expect_fires("t3", "altar")', 'expect_lookback("sacrificed_self")', 'expect_lookback("dies_self")',
    # §613.4 layer 7b SET + 7c modify — clay is printed 1/1, set to 4/4, then a +1/+1 counter -> power 5.
    'on_battlefield("clay")', 'printed_type("clay", "creature")', 'printed_power("clay", 1)', 'printed_toughness("clay", 1)',
    'eff_set_power("forge", "clay", 4, 1)', 'eff_set_toughness("forge", "clay", 4, 1)', 'counter("clay", "p1p1", 1)', 'printed_control("alice", "clay")',
    'expect_power("clay", 5)',
    # §613.4 layer 7d SWITCH — mirror is 2/5; switched -> power 5.
    'on_battlefield("mirror")', 'printed_type("mirror", "creature")', 'printed_power("mirror", 2)', 'printed_toughness("mirror", 5)',
    'eff_switch_pt("sw", "mirror")', 'printed_control("alice", "mirror")', 'expect_power("mirror", 5)',
    # §613 layer 6 GRANT — skyling is granted flying, so a ground blocker can't legally block it (§702.9b via the layer).
    'on_battlefield("skyling")', 'printed_type("skyling", "creature")', 'printed_power("skyling", 2)', 'printed_toughness("skyling", 2)',
    'eff_grant_keyword("wings", "skyling", "flying")', 'printed_control("alice", "skyling")',
    'on_battlefield("gnat")', 'printed_type("gnat", "creature")', 'printed_power("gnat", 1)', 'printed_toughness("gnat", 4)', 'printed_control("bob", "gnat")',
    'attacks("skyling", "bob")', 'blocks("gnat", "skyling")', 'expect_illegal_block("gnat", "skyling")',
    # §613 layer 2 CONTROL — puppet is printed bob's, but a control effect gives alice control.
    'on_battlefield("puppet")', 'printed_type("puppet", "creature")', 'printed_control("bob", "puppet")',
    'eff_gain_control("mind_control", "alice", "puppet", 1)', 'expect_controls("alice", "puppet")',
    # §613 layer 4 TYPE — a land is animated into a creature (added type).
    'on_battlefield("animated")', 'printed_type("animated", "land")', 'eff_add_type("anim", "animated", "creature")',
    'printed_control("alice", "animated")', 'expect_type("animated", "creature")',
    # §613 layer 5 COLOR — a white creature is set to black by a color effect.
    'on_battlefield("painted")', 'printed_color("painted", "white")', 'eff_set_color("paint", "painted", "black", 1)',
    'expect_color("painted", "black")',
    # §613 layer 1 COPY — clone copies dragon (5/6 flying creature); it gets dragon's copiable values.
    'on_battlefield("dragon")', 'printed_type("dragon", "creature")', 'printed_power("dragon", 5)', 'printed_toughness("dragon", 6)', 'printed_keyword("dragon", "flying")', 'printed_control("bob", "dragon")',
    'on_battlefield("clone")', 'eff_copy("clonefx", "clone", "dragon", 1)', 'printed_control("alice", "clone")',
    'expect_power("clone", 5)', 'expect_type("clone", "creature")', 'expect_keyword("clone", "flying")',
    # §613 layer 3 TEXT-CHANGE — morpher's "Forest" subtype is changed to "Island".
    'on_battlefield("morpher")', 'printed_subtype("morpher", "Forest")', 'eff_text_change("txt", "morpher", "Forest", "Island")',
    'printed_control("alice", "morpher")', 'expect_subtype("morpher", "Island")',
    # §613.7 TIMESTAMP — two "set power" effects on statue (printed 1/1): the LATER (ts 5) wins over ts 2.
    'on_battlefield("statue")', 'printed_type("statue", "creature")', 'printed_power("statue", 1)', 'printed_toughness("statue", 1)', 'printed_control("alice", "statue")',
    'eff_set_power("first", "statue", 3, 2)', 'eff_set_power("second", "statue", 7, 5)', 'expect_power("statue", 7)',
    # §615 PREVENT — flamer's combat damage to shielded is prevented; shielded (life 2) survives.
    'is_player("shielded")', 'life("shielded", 2)',
    'on_battlefield("flamer")', 'printed_type("flamer", "creature")', 'printed_power("flamer", 5)', 'printed_toughness("flamer", 5)', 'printed_control("alice", "flamer")',
    'attacks("flamer", "shielded")', 'repl_prevent_damage("ward", "flamer", "shielded")', 'expect_no_loss("shielded")',
    # §614 ETB REPLACEMENTS — the resolving elemental enters tapped and with two +1/+1 counters.
    'repl_enters_tapped("comes_tapped", "elemental")', 'repl_enters_with_counter("comes_big", "elemental", "p1p1", 2)',
    'expect_etb_tapped("elemental")', 'expect_etb_counter("elemental", "p1p1", 2)',
    # §115/§608.2b TARGETING — smite (red) targets a creature with protection from red; its only target
    # is illegal, so it fizzles (doesn't resolve).
    'on_battlefield("warded")', 'printed_type("warded", "creature")', 'printed_power("warded", 2)', 'printed_toughness("warded", 2)', 'printed_control("bob", "warded")', 'protection_from("warded", "red")',
    'on_stack("smite", 5)', 'spell_color("smite", "red")', 'targets("smite", "warded")', 'expect_fizzle("smite")',
    # §122.1b KEYWORD COUNTER — a flying counter grants flying.
    'on_battlefield("birdcage")', 'printed_type("birdcage", "creature")', 'printed_power("birdcage", 1)', 'printed_toughness("birdcage", 1)', 'printed_control("alice", "birdcage")',
    'counter("birdcage", "flying", 1)', 'is_keyword("flying")', 'expect_keyword("birdcage", "flying")',
    # §700.2 MODAL — charm offers damage/draw; the controller chose damage.
    'spell_mode("charm", "damage")', 'spell_mode("charm", "draw")', 'chose_mode("charm", "damage")', 'expect_mode("charm", "damage")',
    # §611.2 DURATION — a giant-growth-style pump is until end of turn (the driver removes it at cleanup).
    'until_eot("pump")', 'expect_ends_cleanup("pump")',
]


def _rules(p: Program) -> None:
    p.decl("creature", [("c", "symbol")])
    p.rule("creature(C)", ["on_battlefield(C)", 'has_type(C, "creature")'])
    p.comment("§505.1 main phases, INTERPRETED from rules.txt by build_turn_actions (not hardcoded).")
    p.decl("main_phase", [("s", "symbol")])
    p.facts([f'main_phase("{m}")' for m in _main_phases()])
    p.blank()
    p.comment("§613 — THE LAYER SYSTEM. A characteristic is its base value with continuous effects")
    p.comment("applied in layer order (the order/categories come from datalog/layers.dl). Implemented:")
    p.comment("layer 6 (abilities) and layer 7 (power/toughness, sublayered 7b set -> 7c modify -> 7d switch).")
    p.blank()
    p.comment("§613 layer 1 — copiable values: a copy effect makes C's copiable characteristics those of")
    p.comment("the copied object; otherwise they're C's own printed values. Layers 3-7 build on copiable_*.")
    p.decl("copy_ts", [("c", "symbol"), ("ts", "number")])
    p.rule("copy_ts(C, M)", ["eff_copy(_, C, _, _)", "M = max T : { eff_copy(_, C, _, T) }"], note="§613.7 latest copy wins")
    p.decl("copy_of", [("c", "symbol"), ("original", "symbol")])
    p.rule("copy_of(C, O)", ["eff_copy(_, C, O, Ts)", "copy_ts(C, Ts)"])
    for ch, base in [("type", "printed_type"), ("color", "printed_color"), ("keyword", "printed_keyword"),
                     ("subtype", "printed_subtype"), ("power", "printed_power"), ("toughness", "printed_toughness")]:
        typ = "number" if ch in ("power", "toughness") else "symbol"
        p.decl(f"copiable_{ch}", [("c", "symbol"), ("v", typ)])
        p.rule(f"copiable_{ch}(C, V)", ["copy_of(C, O)", f"{base}(O, V)"])
        p.rule(f"copiable_{ch}(C, V)", [f"{base}(C, V)", "!copy_of(C, _)"])
    p.blank()
    p.comment("§613 layer 2 — control: the latest control-changing effect overrides the printed controller.")
    p.decl("control_ts", [("c", "symbol"), ("ts", "number")])
    p.rule("control_ts(C, M)", ["eff_gain_control(_, _, C, _)", "M = max T : { eff_gain_control(_, _, C, T) }"])
    p.decl("controls", [("p", "symbol"), ("c", "symbol")])
    p.rule("controls(P, C)", ["eff_gain_control(_, P, C, Ts)", "control_ts(C, Ts)"])
    p.rule("controls(P, C)", ["printed_control(P, C)", "!eff_gain_control(_, _, C, _)"])
    p.blank()
    p.comment("§613 layer 3 — text-changing: a text-change effect remaps a subtype word (e.g. Forest->Island).")
    p.decl("subtype", [("c", "symbol"), ("st", "symbol")])
    p.rule("subtype(C, New)", ["copiable_subtype(C, ST)", "eff_text_change(_, C, ST, New)"])
    p.rule("subtype(C, ST)", ["copiable_subtype(C, ST)", "!eff_text_change(_, C, ST, _)"])
    p.blank()
    p.comment("§613 layer 4 — type: copiable types plus added, minus removed.")
    p.decl("has_type", [("c", "symbol"), ("t", "symbol")])
    p.rule("has_type(C, T)", ["copiable_type(C, T)", "!eff_remove_type(_, C, T)"])
    p.rule("has_type(C, T)", ["eff_add_type(_, C, T)", "!eff_remove_type(_, C, T)"])
    p.blank()
    p.comment("§613 layer 5 — color: the latest color-setting effect overrides the copiable color.")
    p.decl("color_ts", [("c", "symbol"), ("ts", "number")])
    p.rule("color_ts(C, M)", ["eff_set_color(_, C, _, _)", "M = max T : { eff_set_color(_, C, _, T) }"])
    p.decl("color", [("c", "symbol"), ("col", "symbol")])
    p.rule("color(C, Col)", ["eff_set_color(_, C, Col, Ts)", "color_ts(C, Ts)"])
    p.rule("color(C, Col)", ["copiable_color(C, Col)", "!eff_set_color(_, C, _, _)"])
    p.blank()
    p.comment("§613 layer 6 — abilities: copiable keywords plus granted, minus removed.")
    p.decl("has_keyword", [("c", "symbol"), ("kw", "symbol")])
    p.rule("has_keyword(C, K)", ["copiable_keyword(C, K)", "!eff_remove_keyword(_, C, K)"])
    p.rule("has_keyword(C, K)", ["eff_grant_keyword(_, C, K)", "!eff_remove_keyword(_, C, K)"])
    p.rule("has_keyword(C, K)", ["counter(C, K, N)", "N >= 1", "is_keyword(K)", "!eff_remove_keyword(_, C, K)"], note="§122.1b keyword counter")
    p.blank()
    p.comment("§702 keyword vocabulary — the interpreted keyword-ability roster (build_keyword_ability_index).")
    p.comment("The engine DEPENDS on this: every keyword it grants must be a defined §702 ability (see unknown_keyword conformance).")
    p.decl("keyword_ability", [("name", "symbol")])
    p.facts([f'keyword_ability("{name}")' for _r, name in _keyword_ability_roster()])
    p.blank()
    p.comment("§613.4 layer 7b — the latest set effect overrides the copiable base (7a CDA folded in).")
    p.decl("setp_ts", [("c", "symbol"), ("ts", "number")])
    p.rule("setp_ts(C, M)", ["eff_set_power(_, C, _, _)", "M = max T : { eff_set_power(_, C, _, T) }"])
    p.decl("set_power", [("c", "symbol"), ("n", "number")])
    p.rule("set_power(C, N)", ["eff_set_power(_, C, N, Ts)", "setp_ts(C, Ts)"])
    p.decl("sett_ts", [("c", "symbol"), ("ts", "number")])
    p.rule("sett_ts(C, M)", ["eff_set_toughness(_, C, _, _)", "M = max T : { eff_set_toughness(_, C, _, T) }"])
    p.decl("set_toughness", [("c", "symbol"), ("n", "number")])
    p.rule("set_toughness(C, N)", ["eff_set_toughness(_, C, N, Ts)", "sett_ts(C, Ts)"])
    p.decl("base_power", [("c", "symbol"), ("n", "number")])
    p.rule("base_power(C, N)", ["set_power(C, N)"])
    p.rule("base_power(C, N)", ["copiable_power(C, N)", "!set_power(C, _)"])
    p.decl("base_toughness", [("c", "symbol"), ("n", "number")])
    p.rule("base_toughness(C, N)", ["set_toughness(C, N)"])
    p.rule("base_toughness(C, N)", ["copiable_toughness(C, N)", "!set_toughness(C, _)"])
    p.comment("§613.4 layer 7c — modify: +1/+1 & -1/-1 counters and P/T modifiers, on top of the set base.")
    p.decl("pt7c_power", [("c", "symbol"), ("n", "number")])
    p.rule("pt7c_power(C, N)", ["base_power(C, B)", 'P = sum X : { counter(C, "p1p1", X) }', 'M = sum X : { counter(C, "m1m1", X) }', "E = sum X : { mod_power(C, X) }", "N = B + P - M + E"])
    p.decl("pt7c_toughness", [("c", "symbol"), ("n", "number")])
    p.rule("pt7c_toughness(C, N)", ["base_toughness(C, B)", 'P = sum X : { counter(C, "p1p1", X) }', 'M = sum X : { counter(C, "m1m1", X) }', "E = sum X : { mod_toughness(C, X) }", "N = B + P - M + E"])
    p.comment("§613.4 layer 7d — switch: P/T swap; two switches cancel, so apply parity of the count.")
    p.decl("switched", [("c", "symbol")])
    p.rule("switched(C)", ["eff_switch_pt(_, C)", "N = count : { eff_switch_pt(_, C) }", "N % 2 = 1"])
    p.decl("power", [("c", "symbol"), ("n", "number")])
    p.rule("power(C, N)", ["pt7c_power(C, N)", "!switched(C)"])
    p.rule("power(C, N)", ["pt7c_toughness(C, N)", "switched(C)"])
    p.decl("toughness", [("c", "symbol"), ("n", "number")])
    p.rule("toughness(C, N)", ["pt7c_toughness(C, N)", "!switched(C)"])
    p.rule("toughness(C, N)", ["pt7c_power(C, N)", "switched(C)"])
    p.blank()
    p.comment("§702 prohibition/evasion — TRANSPILED from rules.txt by transpile.py.")
    p.comment("The engine DEPENDS on these generated rules (combat 'blocked' below, dies via !cant_be_destroyed).")
    p.decl("cant_attack", [("c", "symbol")])
    p.decl("cant_be_destroyed", [("c", "symbol")])
    p.decl("illegal_block", [("b", "symbol"), ("a", "symbol")])
    p.decl("n_blockers", [("a", "symbol"), ("n", "number")])
    p.rule("n_blockers(A, N)", ["blocks(_, A)", "N = count : { blocks(_, A) }"])
    for _num, dl in _transpiled("702", TRANSPILED_702):
        p.raw(dl)
    p.blank()
    p.comment("§510 — combat, gated by the combat damage step (turn <-> combat).")
    p.comment("Combat respects the transpiled illegal_block: an illegal block neither stops")
    p.comment("the attacker nor exchanges damage, so the attacker hits the player instead.")
    p.decl("combat_now", [])
    p.rule("combat_now()", ['current_step("combat_damage")'])
    p.decl("blocked", [("a", "symbol")])
    p.rule("blocked(A)", ["blocks(B, A)", "!illegal_block(B, A)"], note="only a LEGAL block stops an attacker")
    p.decl("deals", [("s", "symbol"), ("t", "symbol"), ("n", "number")])
    p.decl("prevented", [("src", "symbol"), ("tgt", "symbol")])
    p.rule("prevented(S, T)", ["repl_prevent_damage(_, S, T)"], note="§615 — prevented damage isn't dealt")
    p.rule("deals(A, B, N)", ["combat_now()", "blocks(B, A)", "!illegal_block(B, A)", "!cant_attack(A)", "!prevented(A, B)", "power(A, N)"])
    p.rule("deals(B, A, N)", ["combat_now()", "blocks(B, A)", "!illegal_block(B, A)", "!cant_attack(A)", "!prevented(B, A)", "power(B, N)"])
    p.rule("deals(A, D, N)", ["combat_now()", "attacks(A, D)", "is_player(D)", "!blocked(A)", "!cant_attack(A)", "!prevented(A, D)", "power(A, N)"], note="a creature that can't attack (§702.3b) or whose damage is prevented (§615) deals none")
    p.blank()
    p.decl("withering", [("s", "symbol")])
    p.rule("withering(S)", ['has_keyword(S, "wither")'])
    p.rule("withering(S)", ['has_keyword(S, "infect")'])
    p.decl("marked", [("c", "symbol"), ("n", "number")])
    p.rule("marked(C, N)", ["creature(C)", "deals(_, C, _)", "N = sum X : { deals(S, C, X), !withering(S) }"])
    p.decl("combat_m1m1", [("c", "symbol"), ("n", "number")])
    p.rule("combat_m1m1(C, N)", ["creature(C)", "deals(S0, C, _)", "withering(S0)", "N = sum X : { deals(S, C, X), withering(S) }"])
    p.decl("combat_poison", [("p", "symbol"), ("n", "number")])
    p.rule("combat_poison(P, N)", ["is_player(P)", "deals(S0, P, _)", 'has_keyword(S0, "infect")', 'N = sum X : { deals(S, P, X), has_keyword(S, "infect") }'])
    p.decl("player_damage", [("p", "symbol"), ("n", "number")])
    p.rule("player_damage(P, N)", ["is_player(P)", "deals(_, P, _)", 'N = sum X : { deals(S, P, X), !has_keyword(S, "infect") }'])
    p.blank()
    p.comment("WIRING — combat results update effective toughness / poison / life.")
    p.decl("eff_toughness", [("c", "symbol"), ("n", "number")])
    p.rule("eff_toughness(C, N)", ["toughness(C, T)", "combat_m1m1(C, M)", "N = T - M"])
    p.rule("eff_toughness(C, N)", ["toughness(C, N)", "!combat_m1m1(C, _)"])
    p.decl("total_poison", [("p", "symbol"), ("n", "number")])
    p.rule("total_poison(P, N)", ["poison(P, B)", "combat_poison(P, M)", "N = B + M"])
    p.rule("total_poison(P, N)", ["poison(P, N)", "!combat_poison(P, _)"])
    p.decl("remaining_life", [("p", "symbol"), ("n", "number")])
    p.rule("remaining_life(P, N)", ["life(P, B)", "player_damage(P, M)", "N = B - M"])
    p.rule("remaining_life(P, N)", ["life(P, N)", "!player_damage(P, _)"])
    p.blank()
    p.comment("§704 — state-based actions over the wired state.")
    p.decl("dies", [("c", "symbol")])
    p.rule("dies(C)", ["creature(C)", "eff_toughness(C, T)", "T <= 0", "!cant_be_destroyed(C)"], note="§704.5f + §702.12b via transpiled cant_be_destroyed")
    p.rule("dies(C)", ["creature(C)", "eff_toughness(C, T)", "T > 0", "marked(C, D)", "D >= T", "!cant_be_destroyed(C)"], note="§704.5g + §702.12b")
    p.rule("dies(C)", ["creature(C)", "deals(S, C, N)", "N >= 1", 'has_keyword(S, "deathtouch")', "!cant_be_destroyed(C)"], note="§702.2b + §702.12b")
    p.comment("§104 numeric loss thresholds, INTERPRETED from rules.txt by build_ending (not hardcoded).")
    p.decl("loss_threshold", [("condition", "symbol"), ("n", "number")])
    p.facts([f'loss_threshold("{c}", {n})' for _r, c, n in _loss_thresholds()])
    p.decl("loses_game", [("p", "symbol")])
    p.rule("loses_game(P)", ["remaining_life(P, L)", 'loss_threshold("life_zero", T)', "L <= T"], note="§704.5a — threshold interpreted into ending.dl")
    p.rule("loses_game(P)", ["total_poison(P, N)", 'loss_threshold("poison_ten", T)', "N >= T"], note="§704.5c")
    p.blank()
    p.comment("§701.8a zone movement — TRANSPILED. A creature put into the graveyard by")
    p.comment("an SBA moves via the destroy action; the driver applies zone_change generically.")
    p.decl("do_destroy", [("c", "symbol")])
    p.rule("do_destroy(C)", ["dies(C)"])
    p.comment("§4 zone-crossing restrictions, INTERPRETED from rules.txt by build_zones.")
    p.decl("cant_enter", [("type", "symbol"), ("zone", "symbol")])
    p.decl("cant_leave", [("type", "symbol"), ("zone", "symbol")])
    p.facts(_zone_restriction_facts())
    p.comment("701.8a proposes the move; it's blocked if the object's type can't leave the source")
    p.comment("zone (§400.4b, e.g. a conspiracy can't leave command) or can't enter the destination (§400.4a).")
    p.decl("zone_move_proposed", [("o", "symbol"), ("f", "symbol"), ("t", "symbol")])
    for _num, dl in _transpiled("701", {"701.8a"}):
        p.raw(dl.replace("zone_change(", "zone_move_proposed(", 1))
    p.decl("blocked_move", [("o", "symbol"), ("f", "symbol"), ("t", "symbol")])
    p.rule("blocked_move(O, F, T)", ["zone_move_proposed(O, F, T)", "has_type(O, Ty)", "cant_leave(Ty, F)"], note="§400.4b")
    p.rule("blocked_move(O, F, T)", ["zone_move_proposed(O, F, T)", "has_type(O, Ty)", "cant_enter(Ty, T)"], note="§400.4a")
    p.decl("zone_change", [("o", "symbol"), ("f", "symbol"), ("t", "symbol")])
    p.rule("zone_change(O, F, T)", ["zone_move_proposed(O, F, T)", "!blocked_move(O, F, T)"])
    p.blank()
    p.comment("§117.1a — can_cast = timing + affordability + legal targets.")
    p.decl("can_afford", [("p", "symbol"), ("s", "symbol")])
    p.rule("can_afford(P, S)", ["in_hand(P, S)", "mana_cost(S, C)", "mana_available(P, M)", "M >= C"])
    p.decl("illegal_target", [("s", "symbol"), ("t", "symbol")])
    p.rule("illegal_target(S, T)", ["targets(S, T)", 'has_keyword(T, "shroud")'], note="§702.18")
    p.rule("illegal_target(S, T)", ["targets(S, T)", 'has_keyword(T, "hexproof")', "in_hand(P, S)", "controls(TC, T)", "P != TC"], note="§702.11b")
    p.rule("illegal_target(S, T)", ["targets(S, T)", "spell_color(S, Col)", "protection_from(T, Col)"], note="§702.16e protection")
    p.rule("illegal_target(S, T)", ["targets(S, T)", "cant_be_targeted(T)"], note="effect: can't be the target")
    p.decl("bad_target", [("s", "symbol")])
    p.rule("bad_target(S)", ["illegal_target(S, _)"])
    p.decl("target_ok", [("s", "symbol")])
    p.rule("target_ok(S)", ["in_hand(_, S)", "!bad_target(S)"])
    p.comment("§205.4 supertype semantics, INTERPRETED from rules.txt by build_supertypes (supertype_rule).")
    p.decl("supertype_rule", [("supertype", "symbol"), ("subject", "symbol"), ("rule", "symbol")])
    p.facts(_supertype_rule_facts())
    p.decl("legendary", [("o", "symbol")])
    p.rule("legendary(O)", ["has_supertype(O, Sup)", 'supertype_rule(Sup, "permanent", "legend_rule")'])
    p.comment("§205.4e legendary-spell casting restriction: a legendary instant or sorcery (a spell whose")
    p.comment("type resolves to the graveyard, not the battlefield) can't be cast unless its controller")
    p.comment("controls a legendary creature or planeswalker. The restriction's existence is interpreted.")
    p.decl("legend_cast_restricted", [("s", "symbol")])
    p.rule("legend_cast_restricted(S)", ["in_hand(_, S)", "has_supertype(S, Sup)", 'supertype_rule(Sup, "spell", "legend_cast_restriction")', "spell_type(S, T)", 'resolves_to(T, "graveyard")'])
    p.decl("controls_legendary_permanent", [("p", "symbol")])
    p.rule("controls_legendary_permanent(P)", ["controls(P, O)", "legendary(O)", "has_type(O, Ty)", 'Ty = "creature"'])
    p.rule("controls_legendary_permanent(P)", ["controls(P, O)", "legendary(O)", "has_type(O, Ty)", 'Ty = "planeswalker"'])
    p.decl("cant_cast_legend", [("p", "symbol"), ("s", "symbol")])
    p.rule("cant_cast_legend(P, S)", ["in_hand(P, S)", "legend_cast_restricted(S)", "!controls_legendary_permanent(P)"], note="§205.4e")
    p.comment("§3 casting timing, INTERPRETED from rules.txt by build_casting (cast_permission).")
    p.comment("speed 'instant' -> any priority; 'sorcery' -> active player, a main phase, empty stack.")
    p.decl("cast_permission", [("type", "symbol"), ("action", "symbol"), ("speed", "symbol")])
    p.facts([f'cast_permission("{t}", "{a}", "{sp}")' for _n, t, a, sp in _casting_perms()])
    p.decl("can_cast", [("p", "symbol"), ("s", "symbol")])
    p.rule("can_cast(P, S)", ["in_hand(P, S)", "has_priority(P)", "spell_type(S, T)", 'cast_permission(T, "cast", "instant")', "can_afford(P, S)", "target_ok(S)", "!cant_cast_legend(P, S)"])
    p.rule("can_cast(P, S)", ["in_hand(P, S)", "has_priority(P)", "active_player(P)", "spell_type(S, T)", 'cast_permission(T, "cast", "sorcery")', "current_step(St)", "main_phase(St)", "!on_stack(_, _)", "can_afford(P, S)", "target_ok(S)", "!cant_cast_legend(P, S)"])
    p.blank()
    p.comment("§608 — stack resolution; §117 priority; §500 — step advancement (for the driver).")
    p.decl("stack_top", [("o", "symbol")])
    p.rule("stack_top(O)", ["on_stack(O, Pos)", "Pos = max P : { on_stack(_, P) }"])
    p.comment("§608.2b — a spell/ability that targets, but whose targets are ALL now illegal, doesn't resolve (fizzles).")
    p.decl("has_legal_target", [("s", "symbol")])
    p.rule("has_legal_target(S)", ["targets(S, T)", "!illegal_target(S, T)"])
    p.decl("fizzles", [("s", "symbol")])
    p.rule("fizzles(S)", ["on_stack(S, _)", "targets(S, _)", "!has_legal_target(S)"])
    p.comment("§117.4 — priority passes; the top of the stack resolves only when ALL players have passed.")
    p.decl("resolves", [("o", "symbol")])
    p.rule("resolves(O)", ["stack_top(O)", 'all_passed("yes")', "!fizzles(O)"])
    p.decl("removed_by_fizzle", [("o", "symbol")])
    p.rule("removed_by_fizzle(O)", ["stack_top(O)", 'all_passed("yes")', "fizzles(O)"], note="§608.2b leaves the stack, no effect")
    p.comment("§3 per-type resolution zone, INTERPRETED from rules.txt by build_casting (resolves_to).")
    p.comment("A resolving spell enters the battlefield iff its card type resolves there (permanent types).")
    p.decl("resolves_to", [("type", "symbol"), ("zone", "symbol")])
    p.facts([f'resolves_to("{t}", "{z}")' for _n, t, z in _casting_resolves()])
    p.decl("enters_battlefield", [("o", "symbol")])
    p.rule("enters_battlefield(O)", ["resolves(O)", "spell_type(O, T)", 'resolves_to(T, "battlefield")', '!cant_enter(T, "battlefield")'], note="§608.3 / §400.4a")
    p.comment("§614 — enters-the-battlefield replacements (cards: 'enters tapped', 'enters with N counters').")
    p.decl("enters_tapped", [("c", "symbol")])
    p.rule("enters_tapped(C)", ["enters_battlefield(C)", "repl_enters_tapped(_, C)"])
    p.decl("enters_with_counter", [("c", "symbol"), ("kind", "symbol"), ("n", "number")])
    p.rule("enters_with_counter(C, K, N)", ["enters_battlefield(C)", "repl_enters_with_counter(_, C, K, N)"])
    p.blank()
    p.comment("§700.2 modal spells — the chosen mode must be one the spell offers; the spell's effect")
    p.comment("is gated on active_mode. A choice not among the offered modes is illegal.")
    p.decl("active_mode", [("s", "symbol"), ("mode", "symbol")])
    p.rule("active_mode(S, M)", ["spell_mode(S, M)", "chose_mode(S, M)"])
    p.decl("illegal_mode_choice", [("s", "symbol")])
    p.rule("illegal_mode_choice(S)", ["chose_mode(S, M)", "!spell_mode(S, M)"])
    p.comment("§611.2 — until-end-of-turn continuous effects end during cleanup (the driver removes them).")
    p.decl("ends_at_cleanup", [("e", "symbol")])
    p.rule("ends_at_cleanup(E)", ["until_eot(E)"])
    p.blank()
    p.decl("step", [("idx", "number"), ("name", "symbol")])
    for i, s in enumerate(STEPS):
        p.fact(f'step({i}, "{s}")')
    p.decl("next_step", [("cur", "symbol"), ("nxt", "symbol")])
    p.rule("next_step(A, B)", ["step(I, A)", "step(J, B)", "J = I + 1"])
    p.decl("advance_to", [("nxt", "symbol")])
    p.rule("advance_to(N)", ["current_step(S)", "next_step(S, N)"], note="§500.1")
    p.blank()
    p.comment("§502.3/§504.1 — turn-based actions the driver performs (untap, draw).")
    p.decl("to_untap", [("c", "symbol")])
    p.rule("to_untap(C)", ['current_step("untap")', "active_player(P)", "controls(P, C)", "tapped(C)"], note="§502.3")
    p.decl("to_draw", [("p", "symbol")])
    p.rule("to_draw(P)", ['current_step("draw")', "active_player(P)"], note="§504.1")
    p.comment("§508 — creatures the active player may declare as attackers (for the driver's policy).")
    p.decl("may_attack", [("c", "symbol")])
    p.rule("may_attack(C)", ["active_player(P)", "controls(P, C)", "creature(C)", "!cant_attack(C)"])
    p.blank()
    p.blank()
    p.comment("§603 triggered abilities — events derived from this step's state transitions; matching")
    p.comment("abilities fire and produce a pending effect the driver applies (the trigger->effect loop).")
    p.decl("ev_etb", [("o", "symbol")])
    p.rule("ev_etb(O)", ["enters_battlefield(O)"])
    p.decl("ev_dies", [("c", "symbol")])
    p.rule("ev_dies(C)", ["dies(C)"])
    p.decl("ev_attacks", [("a", "symbol")])
    p.rule("ev_attacks(A)", ["attacks(A, _)", "combat_now()"])
    p.decl("ev_blocks", [("b", "symbol")])
    p.rule("ev_blocks(B)", ["blocks(B, _)", "combat_now()"])
    p.decl("ev_combat_dmg_player", [("s", "symbol"), ("p", "symbol")])
    p.rule("ev_combat_dmg_player(S, P)", ["deals(S, P, _)", "is_player(P)"])
    p.decl("ev_combat_dmg_creature", [("s", "symbol"), ("c", "symbol")])
    p.rule("ev_combat_dmg_creature(S, C)", ["deals(S, C, _)", "creature(C)"])
    p.decl("ev_upkeep", [("p", "symbol")])
    p.rule("ev_upkeep(P)", ['current_step("upkeep")', "active_player(P)"])
    p.decl("ev_end_step", [("p", "symbol")])
    p.rule("ev_end_step(P)", ['current_step("end")', "active_player(P)"])
    p.decl("ev_beginning_of_combat", [("p", "symbol")])      # §507/§603 'at the beginning of combat on your turn'
    p.rule("ev_beginning_of_combat(P)", ['current_step("beginning_of_combat")', "active_player(P)"])
    p.comment("§603.10 look-back events (sacrifice / phase out / counter / a player losing).")
    p.decl("ev_sacrifice", [("o", "symbol")])
    p.rule("ev_sacrifice(O)", ["sacrificed(O)"])
    p.decl("ev_phase_out", [("o", "symbol")])
    p.rule("ev_phase_out(O)", ["phased_out(O)"])
    p.decl("ev_countered", [("o", "symbol")])
    p.rule("ev_countered(O)", ["countered(O)"])
    p.decl("ev_loses_game", [("p", "symbol")])
    p.rule("ev_loses_game(P)", ["loses_game(P)"])
    p.decl("fires", [("ability", "symbol"), ("source", "symbol")])
    p.rule("fires(A, S)", ['has_trigger(A, S, "etb_self")', "ev_etb(S)"], note="§603.2a")
    p.rule("fires(A, S)", ['has_trigger(A, S, "etb_other")', "ev_etb(O)", "O != S"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "dies_self")', "ev_dies(S)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "dies_other")', "ev_dies(O)", "O != S"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "attacks_self")', "ev_attacks(S)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "blocks_self")', "ev_blocks(S)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "combat_damage_to_player")', "ev_combat_dmg_player(S, _)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "combat_damage_to_creature")', "ev_combat_dmg_creature(S, _)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "upkeep")', "ev_upkeep(P)", "controls(P, S)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "end_step")', "ev_end_step(P)", "controls(P, S)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "beginning_of_combat")', "ev_beginning_of_combat(P)", "controls(P, S)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "sacrificed_self")', "ev_sacrifice(S)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "sacrificed_other")', "ev_sacrifice(O)", "O != S"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "phased_out_self")', "ev_phase_out(S)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "countered_self")', "ev_countered(S)"])
    p.rule("fires(A, S)", ['has_trigger(A, S, "player_loses_game")', "ev_loses_game(_)"], note="§603.9")
    p.comment("§603.10 'look back in time' events, INTERPRETED from rules.txt by build_lookback.")
    p.comment("Each engine trigger-event key bridges to its §603.10 phrase; the key is a look-back")
    p.comment("trigger iff the rules say that phrase looks back (so the table is rules-driven, not asserted).")
    p.comment("These triggers are checked against the pre-event state — why the driver snapshots first.")
    p.decl("looks_back_in_time", [("event", "symbol")])
    p.facts([f'looks_back_in_time("{e}")' for e in _lookback_events()])
    p.decl("lookback_bridge", [("event_key", "symbol"), ("phrase", "symbol")])
    p.facts([f'lookback_bridge("{k}", "{ph}")' for k, ph in _LOOKBACK_BRIDGE])
    p.decl("lookback_trigger", [("event_key", "symbol")])
    p.rule("lookback_trigger(K)", ["lookback_bridge(K, P)", "looks_back_in_time(P)"])
    p.comment("pending carries the SOURCE so self-targeting effects (e.g. a +1/+1 counter on itself) resolve.")
    p.decl("pending", [("ability", "symbol"), ("effect", "symbol"), ("amount", "number"), ("target", "symbol"), ("source", "symbol"), ("controller", "symbol")])
    p.rule("pending(A, E, Amt, T, S, P)", ["fires(A, S)", "trigger_effect(A, E, Amt, T)", "controls(P, S)"])
    p.blank()
    p.output("power", "dies", "loses_game", "can_cast", "enters_battlefield", "advance_to",
             "cant_attack", "illegal_block", "cant_be_destroyed", "zone_change", "to_untap", "to_draw",
             "may_attack", "player_damage", "fires", "pending", "enters_tapped", "enters_with_counter",
             "fizzles", "active_mode", "ends_at_cleanup", "lookback_trigger",
             "controls", "creature")    # derived (from printed_*); the driver reads these, not raw state


def build(with_tests: bool) -> str:
    p = Program()
    kind = "full (with tests)" if with_tests else "RULES ONLY (for the driver)"
    p.comment(f"engine{'' if with_tests else '_rules'}.dl — GENERATED by build_engine.py — {kind}.")
    p.comment("All dimensions wired on one game state. Do not edit by hand.")
    p.blank()
    for name, cols in INPUTS:
        p.decl(name, cols)
    p.blank()
    _rules(p)
    if with_tests:
        p.blank()
        p.comment("conformance")
        p.conformance(EXPECT_DECLS, CHECKS)
        p.blank()
        p.comment("scenario under test (one wired game state)")
        for atom in SCENARIOS:
            p.fact(atom)
    return p.text()


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    Path("datalog/engine.dl").write_text(build(with_tests=True), encoding="utf-8")
    Path("datalog/engine_rules.dl").write_text(build(with_tests=False), encoding="utf-8")
    print("wrote datalog/engine.dl and datalog/engine_rules.dl")


if __name__ == "__main__":
    main()
