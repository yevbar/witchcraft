"""Build datalog/state.dl deterministically from explicit structures.

§105/§202.2 colors, §110 permanents/status, §205 type line, §400 zones,
§613.4 layer-7 power/toughness, §704 state-based actions. The SBA family is an
explicit (outcome, subject-var, rule-number, condition) table.
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (run as script from interpreter/)

from pathlib import Path

from interpreter.dlgen import Program
from interpreter.build_enumerations import members

# INTERPRETED from rules.txt (§400.1 zones, §110.4 permanent types) — not hardcoded.
ZONES = members("zone")
PERMANENT_TYPES = members("permanent_type")

INPUTS = [
    ("in_zone", [("o", "symbol"), ("z", "symbol")]),
    ("is_card", [("o", "symbol")]),
    ("is_token", [("o", "symbol")]),
    ("controls", [("p", "symbol"), ("o", "symbol")]),
    ("has_type", [("o", "symbol"), ("t", "symbol")]),       # 205.2
    ("supertype", [("o", "symbol"), ("s", "symbol")]),      # 205.4
    ("name", [("o", "symbol"), ("n", "symbol")]),
    ("cost_pip_color", [("o", "symbol"), ("c", "symbol")]),  # a colored mana symbol in the cost
    ("printed_power", [("o", "symbol"), ("n", "number")]),   # 613.1 starting point
    ("printed_toughness", [("o", "symbol"), ("n", "number")]),
    ("damage", [("o", "symbol"), ("d", "number")]),
    ("loyalty", [("o", "symbol"), ("l", "number")]),
    ("life", [("p", "symbol"), ("l", "number")]),
    ("poison", [("p", "symbol"), ("n", "number")]),
    ("attempted_draw_empty", [("p", "symbol")]),
    ("tapped", [("o", "symbol")]),                           # 110.5
    ("set_power", [("o", "symbol"), ("ts", "number"), ("n", "number")]),       # 7b
    ("set_toughness", [("o", "symbol"), ("ts", "number"), ("n", "number")]),
    ("mod_power", [("o", "symbol"), ("dp", "number")]),      # 7c effect modifier
    ("mod_toughness", [("o", "symbol"), ("dt", "number")]),
    ("counter", [("o", "symbol"), ("kind", "symbol"), ("n", "number")]),       # 7c counters
    ("switched", [("o", "symbol")]),                         # 7d
]

# §704.5 — (outcome relation, subject var, rule number, condition body).
SBAS = [
    ("sba_loses", "P", "704.5a", ["life(P, L)", "L <= 0"]),
    ("sba_loses", "P", "704.5b", ["attempted_draw_empty(P)"]),
    ("sba_loses", "P", "704.5c", ["poison(P, N)", "N >= 10"]),
    ("sba_graveyard", "O", "704.5f", ["creature_perm(O)", "toughness(O, T)", "T <= 0"]),
    ("sba_graveyard", "O", "704.5i", ["planeswalker_perm(O)", "loyalty(O, 0)"]),
    ("sba_destroy", "O", "704.5g", ["creature_perm(O)", "toughness(O, T)", "T > 0", "damage(O, D)", "D >= T"]),
    ("sba_ceases", "O", "704.5d", ["is_token(O)", "in_zone(O, Z)", 'Z != "battlefield"']),
]
SBA_SUBJECT = {"sba_loses": "p", "sba_graveyard": "o", "sba_destroy": "o", "sba_ceases": "o"}

EXPECT_DECLS = [
    ("expect_loses", [("p", "symbol"), ("rule", "symbol")]),
    ("expect_no_loses", [("p", "symbol")]),
    ("expect_graveyard", [("o", "symbol"), ("rule", "symbol")]),
    ("expect_destroy", [("o", "symbol"), ("rule", "symbol")]),
    ("expect_no_destroy", [("o", "symbol")]),
    ("expect_ceases", [("o", "symbol"), ("rule", "symbol")]),
    ("expect_no_ceases", [("o", "symbol")]),
    ("expect_legend", [("p", "symbol"), ("n", "symbol")]),
    ("expect_color", [("o", "symbol"), ("c", "symbol")]),
    ("expect_colorless", [("o", "symbol")]),
    ("expect_monocolored", [("o", "symbol")]),
    ("expect_multicolored", [("o", "symbol")]),
    ("expect_basic", [("o", "symbol")]),
    ("expect_nonbasic", [("o", "symbol")]),
    ("expect_untapped", [("o", "symbol")]),
    ("expect_not_untapped", [("o", "symbol")]),
    ("expect_power", [("o", "symbol"), ("n", "number")]),
    ("expect_toughness", [("o", "symbol"), ("n", "number")]),
]
CHECKS = [
    ("loses", "expect_loses(P, R)", "miss", "sba_loses(P, R)"),
    ("no_loses", "expect_no_loses(P)", "hit", "sba_loses(P, _)"),
    ("graveyard", "expect_graveyard(O, R)", "miss", "sba_graveyard(O, R)"),
    ("destroy", "expect_destroy(O, R)", "miss", "sba_destroy(O, R)"),
    ("no_destroy", "expect_no_destroy(O)", "hit", "sba_destroy(O, _)"),
    ("ceases", "expect_ceases(O, R)", "miss", "sba_ceases(O, R)"),
    ("no_ceases", "expect_no_ceases(O)", "hit", "sba_ceases(O, _)"),
    ("legend", "expect_legend(P, N)", "miss", "legend_conflict(P, N)"),
    ("color", "expect_color(O, C)", "miss", "color(O, C)"),
    ("colorless", "expect_colorless(O)", "miss", "colorless(O)"),
    ("monocolored", "expect_monocolored(O)", "miss", "monocolored(O)"),
    ("multicolored", "expect_multicolored(O)", "miss", "multicolored(O)"),
    ("basic", "expect_basic(O)", "miss", "basic_land(O)"),
    ("nonbasic", "expect_nonbasic(O)", "miss", "nonbasic_land(O)"),
    ("untapped", "expect_untapped(O)", "miss", "untapped(O)"),
    ("not_untapped", "expect_not_untapped(O)", "hit", "untapped(O)"),
    ("power", "expect_power(O, N)", "miss", "power(O, N)", "O", '"-"'),
    ("toughness", "expect_toughness(O, N)", "miss", "toughness(O, N)", "O", '"-"'),
]

SCENARIOS = [
    # players (SBAs)
    'life("alice", 0)', 'life("bob", 5)', 'life("carol", 5)', 'life("dave", 5)', 'life("eve", 5)',
    'poison("carol", 10)', 'poison("dave", 9)', 'attempted_draw_empty("eve")',
    'expect_loses("alice", "704.5a")', 'expect_loses("carol", "704.5c")', 'expect_loses("eve", "704.5b")',
    'expect_no_loses("bob")', 'expect_no_loses("dave")',
    # zero-toughness creature -> graveyard
    'in_zone("zerotough", "battlefield")', 'is_card("zerotough")', 'has_type("zerotough", "creature")', 'printed_power("zerotough", 2)', 'printed_toughness("zerotough", 0)',
    'expect_graveyard("zerotough", "704.5f")',
    # lethal damage vs survivor
    'in_zone("lethal", "battlefield")', 'is_card("lethal")', 'has_type("lethal", "creature")', 'printed_power("lethal", 2)', 'printed_toughness("lethal", 3)', 'damage("lethal", 3)',
    'in_zone("survivor", "battlefield")', 'is_card("survivor")', 'has_type("survivor", "creature")', 'printed_power("survivor", 2)', 'printed_toughness("survivor", 3)', 'damage("survivor", 2)',
    'expect_destroy("lethal", "704.5g")', 'expect_no_destroy("survivor")',
    # planeswalker loyalty 0
    'in_zone("pw0", "battlefield")', 'is_card("pw0")', 'has_type("pw0", "planeswalker")', 'loyalty("pw0", 0)',
    'expect_graveyard("pw0", "704.5i")',
    # token off battlefield ceases
    'in_zone("tok_gy", "graveyard")', 'is_token("tok_gy")',
    'in_zone("tok_bf", "battlefield")', 'is_token("tok_bf")', 'has_type("tok_bf", "creature")', 'printed_power("tok_bf", 1)', 'printed_toughness("tok_bf", 1)',
    'expect_ceases("tok_gy", "704.5d")', 'expect_no_ceases("tok_bf")',
    # legend rule
    'in_zone("jace1", "battlefield")', 'is_card("jace1")', 'supertype("jace1", "legendary")', 'name("jace1", "Jace")', 'controls("alice", "jace1")', 'has_type("jace1", "planeswalker")', 'loyalty("jace1", 5)',
    'in_zone("jace2", "battlefield")', 'is_card("jace2")', 'supertype("jace2", "legendary")', 'name("jace2", "Jace")', 'controls("alice", "jace2")', 'has_type("jace2", "planeswalker")', 'loyalty("jace2", 4)',
    'expect_legend("alice", "Jace")',
    # colors
    'in_zone("boros", "hand")', 'is_card("boros")', 'cost_pip_color("boros", "w")', 'cost_pip_color("boros", "r")',
    'expect_color("boros", "w")', 'expect_color("boros", "r")', 'expect_multicolored("boros")',
    'in_zone("ornith", "hand")', 'is_card("ornith")', 'has_type("ornith", "artifact")',
    'expect_colorless("ornith")',
    'in_zone("bear", "battlefield")', 'is_card("bear")', 'has_type("bear", "creature")', 'cost_pip_color("bear", "g")', 'printed_power("bear", 2)', 'printed_toughness("bear", 2)',
    'expect_monocolored("bear")', 'expect_color("bear", "g")',
    # types
    'in_zone("juggernaut", "battlefield")', 'is_card("juggernaut")', 'has_type("juggernaut", "artifact")', 'has_type("juggernaut", "creature")', 'printed_power("juggernaut", 5)', 'printed_toughness("juggernaut", 3)',
    'in_zone("forest", "battlefield")', 'is_card("forest")', 'has_type("forest", "land")', 'supertype("forest", "basic")',
    'in_zone("academy", "battlefield")', 'is_card("academy")', 'has_type("academy", "land")',
    'expect_basic("forest")', 'expect_nonbasic("academy")',
    # status
    'in_zone("tapped_one", "battlefield")', 'is_card("tapped_one")', 'has_type("tapped_one", "creature")', 'printed_power("tapped_one", 1)', 'printed_toughness("tapped_one", 1)', 'tapped("tapped_one")',
    'expect_not_untapped("tapped_one")', 'expect_untapped("bear")',
    # layer 7
    'in_zone("counter_bear", "battlefield")', 'is_card("counter_bear")', 'has_type("counter_bear", "creature")', 'printed_power("counter_bear", 2)', 'printed_toughness("counter_bear", 2)', 'counter("counter_bear", "p1p1", 1)',
    'expect_power("counter_bear", 3)', 'expect_toughness("counter_bear", 3)',
    'in_zone("dying", "battlefield")', 'is_card("dying")', 'has_type("dying", "creature")', 'printed_power("dying", 1)', 'printed_toughness("dying", 1)', 'counter("dying", "m1m1", 1)',
    'expect_toughness("dying", 0)', 'expect_graveyard("dying", "704.5f")',
    'in_zone("pumped", "battlefield")', 'is_card("pumped")', 'has_type("pumped", "creature")', 'printed_power("pumped", 2)', 'printed_toughness("pumped", 2)', 'mod_power("pumped", 2)', 'mod_toughness("pumped", 2)',
    'expect_power("pumped", 4)', 'expect_toughness("pumped", 4)',
    'in_zone("set_pump", "battlefield")', 'is_card("set_pump")', 'has_type("set_pump", "creature")', 'printed_power("set_pump", 5)', 'printed_toughness("set_pump", 5)', 'set_power("set_pump", 1, 1)', 'set_toughness("set_pump", 1, 1)', 'counter("set_pump", "p1p1", 2)',
    'expect_power("set_pump", 3)', 'expect_toughness("set_pump", 3)',
    'in_zone("switcheroo", "battlefield")', 'is_card("switcheroo")', 'has_type("switcheroo", "creature")', 'printed_power("switcheroo", 4)', 'printed_toughness("switcheroo", 1)', 'switched("switcheroo")',
    'expect_power("switcheroo", 1)', 'expect_toughness("switcheroo", 4)',
]


def build() -> str:
    p = Program()
    p.comment("state.dl — GENERATED by build_state.py (deterministic). Do not edit by hand.")
    p.comment("MTG state ontology, characteristics (§105/§202/§205/§613.4), and SBAs (§704).")
    p.blank()
    p.decl("zone", [("z", "symbol")])
    p.facts([f'zone("{z}")' for z in ZONES])
    p.blank()
    for name, cols in INPUTS:
        p.decl(name, cols)
    p.blank()
    p.decl("object", [("o", "symbol")])
    p.rule("object(O)", ["in_zone(O, _)"])
    p.blank()
    # 110.1 / 110.4 permanents
    p.decl("permanent", [("o", "symbol")])
    p.rule("permanent(O)", ['in_zone(O, "battlefield")', "is_card(O)"], note="§110.1")
    p.rule("permanent(O)", ['in_zone(O, "battlefield")', "is_token(O)"])
    p.decl("permanent_type", [("t", "symbol")])
    p.facts([f'permanent_type("{t}")' for t in PERMANENT_TYPES])
    p.decl("creature_perm", [("o", "symbol")])
    p.rule("creature_perm(O)", ["permanent(O)", 'has_type(O, "creature")'])
    p.decl("planeswalker_perm", [("o", "symbol")])
    p.rule("planeswalker_perm(O)", ["permanent(O)", 'has_type(O, "planeswalker")'])
    p.blank()
    # 105.2 / 202.2 colors
    p.decl("color", [("o", "symbol"), ("c", "symbol")])
    p.rule("color(O, C)", ["cost_pip_color(O, C)"], note="§202.2")
    p.decl("colorless", [("o", "symbol")])
    p.rule("colorless(O)", ["object(O)", "!color(O, _)"], note="§105.2c / §202.2b")
    p.decl("monocolored", [("o", "symbol")])
    p.rule("monocolored(O)", ["object(O)", "1 = count : { color(O, _) }"], note="§105.2a")
    p.decl("multicolored", [("o", "symbol")])
    p.rule("multicolored(O)", ["object(O)", "N = count : { color(O, _) }", "N >= 2"], note="§105.2b")
    p.blank()
    # 205.4 supertypes
    p.decl("is_legendary", [("o", "symbol")])
    p.rule("is_legendary(O)", ['supertype(O, "legendary")'])
    p.decl("basic_land", [("o", "symbol")])
    p.rule("basic_land(O)", ['has_type(O, "land")', 'supertype(O, "basic")'], note="§205.4c")
    p.decl("nonbasic_land", [("o", "symbol")])
    p.rule("nonbasic_land(O)", ['has_type(O, "land")', '!supertype(O, "basic")'], note="§205.4c")
    p.blank()
    # 110.5 status
    p.decl("untapped", [("o", "symbol")])
    p.rule("untapped(O)", ["permanent(O)", "!tapped(O)"], note="§110.5b")
    p.blank()
    # 613.4 layer 7
    p.comment("§613.4 — Layer 7 power/toughness (7b set -> 7c modify -> 7d switch).")
    p.decl("base_power", [("o", "symbol"), ("n", "number")])
    p.rule("base_power(O, N)", ["printed_power(O, N)", "!set_power(O, _, _)"])
    p.rule("base_power(O, N)", ["set_power(O, Ts, N)", "Ts = max T : { set_power(O, T, _) }"], note="§613.4b latest set wins")
    p.decl("base_toughness", [("o", "symbol"), ("n", "number")])
    p.rule("base_toughness(O, N)", ["printed_toughness(O, N)", "!set_toughness(O, _, _)"])
    p.rule("base_toughness(O, N)", ["set_toughness(O, Ts, N)", "Ts = max T : { set_toughness(O, T, _) }"])
    p.decl("delta_power", [("o", "symbol"), ("m", "number")])
    p.rule("delta_power(O, M)", ['base_power(O, _)', 'E = sum D : { mod_power(O, D) }',
                                 'Plus = sum N : { counter(O, "p1p1", N) }', 'Minus = sum N : { counter(O, "m1m1", N) }',
                                 "M = E + Plus - Minus"], note="§613.4c counters + modifiers")
    p.decl("delta_toughness", [("o", "symbol"), ("m", "number")])
    p.rule("delta_toughness(O, M)", ['base_toughness(O, _)', 'E = sum D : { mod_toughness(O, D) }',
                                     'Plus = sum N : { counter(O, "p1p1", N) }', 'Minus = sum N : { counter(O, "m1m1", N) }',
                                     "M = E + Plus - Minus"])
    p.decl("power_pre_switch", [("o", "symbol"), ("n", "number")])
    p.rule("power_pre_switch(O, N)", ["base_power(O, B)", "delta_power(O, M)", "N = B + M"])
    p.decl("toughness_pre_switch", [("o", "symbol"), ("n", "number")])
    p.rule("toughness_pre_switch(O, N)", ["base_toughness(O, B)", "delta_toughness(O, M)", "N = B + M"])
    p.decl("power", [("o", "symbol"), ("n", "number")])
    p.decl("toughness", [("o", "symbol"), ("n", "number")])
    p.rule("power(O, N)", ["power_pre_switch(O, N)", "!switched(O)"], note="§613.4d switch")
    p.rule("power(O, N)", ["toughness_pre_switch(O, N)", "switched(O)"])
    p.rule("toughness(O, N)", ["toughness_pre_switch(O, N)", "!switched(O)"])
    p.rule("toughness(O, N)", ["power_pre_switch(O, N)", "switched(O)"])
    p.blank()
    # 704.5 SBAs (from the SBAS table)
    p.comment("§704.5 — State-Based Actions (read the COMPUTED power/toughness).")
    for rel in dict.fromkeys(r[0] for r in SBAS):           # declare each outcome once, in order
        p.decl(rel, [(SBA_SUBJECT[rel], "symbol"), ("rule", "symbol")])
    for rel, var, rule_no, body in SBAS:
        p.rule(f'{rel}({var}, "{rule_no}")', body, note=f"§{rule_no}")
    p.decl("legendary_perm_named", [("p", "symbol"), ("n", "symbol"), ("o", "symbol")])
    p.rule("legendary_perm_named(P, N, O)", ["permanent(O)", "is_legendary(O)", "name(O, N)", "controls(P, O)"])
    p.decl("legend_conflict", [("p", "symbol"), ("n", "symbol")])
    p.rule("legend_conflict(P, N)", ["legendary_perm_named(P, N, _)",
                                     "C = count : { legendary_perm_named(P, N, _) }", "C >= 2"], note="§704.5j")
    p.blank()
    p.output("power", "toughness", "sba_graveyard", "sba_destroy", "sba_loses", "color")
    p.blank()
    p.comment("conformance")
    p.conformance(EXPECT_DECLS, CHECKS)
    p.blank()
    p.comment("scenarios under test")
    for atom in SCENARIOS:
        p.fact(atom)
    return p.text()


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    Path("datalog/state.dl").write_text(build(), encoding="utf-8")
    print("wrote datalog/state.dl")


if __name__ == "__main__":
    main()
