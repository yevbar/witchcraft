"""Build datalog/keywords.dl deterministically from explicit structures.

Combat keyword abilities (§702) layered on a two-step combat-damage model
(§510.4 first-strike step): defender (702.3b), flying/reach (702.9b), menace
(702.111b), deathtouch (702.2b), indestructible (702.12b), trample (702.19),
vigilance (702.20b), haste (702.10b) + summoning sickness (302.6), lifelink
(702.15b), first strike (702.7b), double strike (702.4b). The keywords are an
explicit (rule#, name) table; their rulings are rules over attacks/blocks.
"""

from __future__ import annotations

from pathlib import Path

from dlgen import Program

KEYWORDS = [
    ("702.3", "defender"), ("702.9", "flying"), ("702.17", "reach"),
    ("702.111", "menace"), ("702.2", "deathtouch"), ("702.12", "indestructible"),
    ("702.19", "trample"), ("702.20", "vigilance"), ("702.10", "haste"),
    ("702.15", "lifelink"), ("702.7", "first_strike"), ("702.4", "double_strike"),
    ("702.36", "fear"), ("702.80", "wither"), ("702.90", "infect"),
]

INPUTS = [
    ("has_keyword", [("c", "symbol"), ("kw", "symbol")]),
    ("power", [("c", "symbol"), ("n", "number")]),
    ("toughness", [("c", "symbol"), ("n", "number")]),
    ("is_player", [("p", "symbol")]),
    ("controls", [("p", "symbol"), ("c", "symbol")]),          # for lifelink
    ("entered_this_turn", [("g", "symbol"), ("c", "symbol")]),  # for summoning sickness
    ("attacks", [("g", "symbol"), ("attacker", "symbol"), ("defender", "symbol")]),
    ("blocks", [("g", "symbol"), ("blocker", "symbol"), ("attacker", "symbol")]),
    ("artifact", [("c", "symbol")]),   # for fear's block restriction
    ("black", [("c", "symbol")]),
]

EXPECT_DECLS = [
    ("expect_cant_attack", [("g", "symbol"), ("c", "symbol")]),
    ("expect_can_attack", [("g", "symbol"), ("c", "symbol")]),
    ("expect_illegal_block", [("g", "symbol"), ("b", "symbol"), ("a", "symbol")]),
    ("expect_legal_block", [("g", "symbol"), ("b", "symbol"), ("a", "symbol")]),
    ("expect_taps", [("g", "symbol"), ("c", "symbol")]),
    ("expect_no_taps", [("g", "symbol"), ("c", "symbol")]),
    ("expect_destroyed", [("g", "symbol"), ("c", "symbol")]),
    ("expect_not_destroyed", [("g", "symbol"), ("c", "symbol")]),
    ("expect_trample", [("g", "symbol"), ("a", "symbol"), ("x", "number")]),
    ("expect_life", [("g", "symbol"), ("p", "symbol"), ("n", "number")]),
    ("expect_m1m1", [("g", "symbol"), ("c", "symbol"), ("n", "number")]),
    ("expect_poison", [("g", "symbol"), ("p", "symbol"), ("n", "number")]),
]
CHECKS = [
    ("cant_attack", "expect_cant_attack(G, C)", "miss", "cant_attack(G, C)"),
    ("can_attack", "expect_can_attack(G, C)", "hit", "cant_attack(G, C)"),
    ("illegal_block", "expect_illegal_block(G, B, A)", "miss", "illegal_block(G, B, A)"),
    ("legal_block", "expect_legal_block(G, B, A)", "hit", "illegal_block(G, B, A)"),
    ("taps", "expect_taps(G, C)", "miss", "attacker_taps(G, C)"),
    ("no_taps", "expect_no_taps(G, C)", "hit", "attacker_taps(G, C)"),
    ("destroyed", "expect_destroyed(G, C)", "miss", "destroyed(G, C)"),
    ("not_destroyed", "expect_not_destroyed(G, C)", "hit", "destroyed(G, C)"),
    ("trample", "expect_trample(G, A, X)", "miss", "trample_to_player(G, A, _, X)", "G", "A"),
    ("life", "expect_life(G, P, N)", "miss", "life_gain(G, P, N)", "G", "P"),
    ("m1m1", "expect_m1m1(G, C, N)", "miss", "gives_m1m1(G, C, N)", "G", "C"),
    ("poison", "expect_poison(G, P, N)", "miss", "gives_poison(G, P, N)", "G", "P"),
]

SCENARIOS = [
    # creatures
    'power("wall_d", 0)', 'toughness("wall_d", 4)', 'has_keyword("wall_d", "defender")',
    'power("drake", 2)', 'toughness("drake", 2)', 'has_keyword("drake", "flying")',
    'power("bear", 2)', 'toughness("bear", 2)',
    'power("spider", 2)', 'toughness("spider", 4)', 'has_keyword("spider", "reach")',
    'power("ogre", 3)', 'toughness("ogre", 3)', 'has_keyword("ogre", "menace")',
    'power("snake", 1)', 'toughness("snake", 1)', 'has_keyword("snake", "deathtouch")',
    'power("giant", 4)', 'toughness("giant", 4)',
    'power("darksteel", 2)', 'toughness("darksteel", 2)', 'has_keyword("darksteel", "indestructible")',
    'power("rhino", 5)', 'toughness("rhino", 5)', 'has_keyword("rhino", "trample")',
    'power("blocker3", 0)', 'toughness("blocker3", 3)',
    'power("knight", 2)', 'toughness("knight", 1)', 'has_keyword("knight", "first_strike")',
    'power("fencer", 2)', 'toughness("fencer", 2)', 'has_keyword("fencer", "double_strike")',
    'power("paladin", 2)', 'toughness("paladin", 2)', 'has_keyword("paladin", "vigilance")',
    'power("cub", 2)', 'toughness("cub", 2)',
    'power("vamp", 2)', 'toughness("vamp", 2)', 'has_keyword("vamp", "lifelink")', 'controls("alice", "vamp")',
    'is_player("bob")',
    # 702.3b defender / 302.6+702.10b haste & summoning sickness
    'gstate("g_def")',
    'expect_cant_attack("g_def", "wall_d")',
    'entered_this_turn("g_sick", "cub")', 'expect_cant_attack("g_sick", "cub")',
    'entered_this_turn("g_haste", "knight")', 'has_keyword("knight", "haste")', 'expect_can_attack("g_haste", "knight")',
    # 702.9b flying / 702.111b menace block legality
    'attacks("g_fly", "drake", "bob")', 'blocks("g_fly", "bear", "drake")', 'expect_illegal_block("g_fly", "bear", "drake")',
    'attacks("g_fly2", "drake", "bob")', 'blocks("g_fly2", "spider", "drake")', 'expect_legal_block("g_fly2", "spider", "drake")',
    'attacks("g_men", "ogre", "bob")', 'blocks("g_men", "bear", "ogre")', 'expect_illegal_block("g_men", "bear", "ogre")',
    'attacks("g_men2", "ogre", "bob")', 'blocks("g_men2", "bear", "ogre")', 'blocks("g_men2", "spider", "ogre")', 'expect_legal_block("g_men2", "bear", "ogre")',
    # 702.20b vigilance — attacking doesn't tap.
    'attacks("g_vig", "paladin", "bob")', 'expect_no_taps("g_vig", "paladin")',
    'attacks("g_vig", "cub", "bob")', 'expect_taps("g_vig", "cub")',
    # 702.2b deathtouch / 702.12b indestructible
    'attacks("g_dt", "snake", "bob")', 'blocks("g_dt", "giant", "snake")', 'expect_destroyed("g_dt", "giant")',
    'attacks("g_ind", "rhino", "bob")', 'blocks("g_ind", "darksteel", "rhino")', 'expect_not_destroyed("g_ind", "darksteel")',
    # 702.7b first strike — knight (2/1 FS) vs bear (2/2): bear dies in FS step, deals nothing back.
    'attacks("g_fs", "knight", "bob")', 'blocks("g_fs", "bear", "knight")',
    'expect_destroyed("g_fs", "bear")', 'expect_not_destroyed("g_fs", "knight")',
    # 702.4b double strike — fencer (2/2 DS) vs giant (4/4): fencer deals 4 (kills), giant deals 4 (kills fencer).
    'attacks("g_ds", "fencer", "bob")', 'blocks("g_ds", "giant", "fencer")',
    'expect_destroyed("g_ds", "giant")', 'expect_destroyed("g_ds", "fencer")',
    # 702.19 trample — rhino (5/5) over blocker3 (0/3): 2 excess.
    'attacks("g_tr", "rhino", "bob")', 'blocks("g_tr", "blocker3", "rhino")', 'expect_trample("g_tr", "rhino", 2)',
    # 702.15b lifelink — vamp (2/2) attacks unblocked, alice gains 2.
    'attacks("g_ll", "vamp", "bob")', 'expect_life("g_ll", "alice", 2)',
    # 702.36b fear — only artifact/black creatures may block.
    'power("fearbeast", 3)', 'toughness("fearbeast", 3)', 'has_keyword("fearbeast", "fear")',
    'power("golem", 2)', 'toughness("golem", 2)', 'artifact("golem")',
    'power("zombie", 2)', 'toughness("zombie", 2)', 'black("zombie")',
    'attacks("g_fear", "fearbeast", "bob")', 'blocks("g_fear", "bear", "fearbeast")', 'expect_illegal_block("g_fear", "bear", "fearbeast")',
    'attacks("g_fear2", "fearbeast", "bob")', 'blocks("g_fear2", "golem", "fearbeast")', 'expect_legal_block("g_fear2", "golem", "fearbeast")',
    'attacks("g_fear3", "fearbeast", "bob")', 'blocks("g_fear3", "zombie", "fearbeast")', 'expect_legal_block("g_fear3", "zombie", "fearbeast")',
    # 702.80 wither — blight (3/3) blocked by bear puts three -1/-1 counters on it.
    'power("blight", 3)', 'toughness("blight", 3)', 'has_keyword("blight", "wither")',
    'attacks("g_wit", "blight", "bob")', 'blocks("g_wit", "bear", "blight")', 'expect_m1m1("g_wit", "bear", 3)',
    # 702.90 infect — plague (2/2) hits bob for 2 poison.
    'power("plague", 2)', 'toughness("plague", 2)', 'has_keyword("plague", "infect")',
    'attacks("g_inf", "plague", "bob")', 'expect_poison("g_inf", "bob", 2)',
]


def build() -> str:
    p = Program()
    p.comment("keywords.dl — GENERATED by build_keywords.py (deterministic). Do not edit by hand.")
    p.comment("MTG combat keyword abilities (§702), two-step combat damage (§510.4).")
    p.blank()
    p.decl("keyword", [("rule", "symbol"), ("name", "symbol")])
    p.facts([f'keyword("{rule}", "{name}")' for rule, name in KEYWORDS])
    p.blank()
    for name, cols in INPUTS:
        p.decl(name, cols)
    p.blank()

    p.decl("blocked", [("g", "symbol"), ("a", "symbol")])
    p.rule("blocked(G, A)", ["blocks(G, _, A)"])
    p.decl("n_blockers", [("g", "symbol"), ("a", "symbol"), ("n", "number")])
    p.rule("n_blockers(G, A, N)", ["blocks(G, _, A)", "N = count : { blocks(G, _, A) }"])
    p.blank()

    # the game states in play (so per-state rules have a domain).
    p.decl("gstate", [("g", "symbol")])
    p.rule("gstate(G)", ["attacks(G, _, _)"])
    p.rule("gstate(G)", ["blocks(G, _, _)"])
    p.rule("gstate(G)", ["entered_this_turn(G, _)"])
    p.blank()
    # §302.6 / §702.10b — summoning sickness; §702.3b — defender. Both block attacking.
    p.decl("summoning_sick", [("g", "symbol"), ("c", "symbol")])
    p.rule("summoning_sick(G, C)", ["entered_this_turn(G, C)", '!has_keyword(C, "haste")'], note="§302.6 / §702.10b")
    p.decl("cant_attack", [("g", "symbol"), ("c", "symbol")])
    p.rule("cant_attack(G, C)", ["gstate(G)", 'has_keyword(C, "defender")'], note="§702.3b")
    p.rule("cant_attack(G, C)", ["summoning_sick(G, C)"])
    p.blank()

    # §702.9b flying, §702.111b menace — illegal blocks.
    p.decl("illegal_block", [("g", "symbol"), ("b", "symbol"), ("a", "symbol")])
    p.rule("illegal_block(G, B, A)",
           ["blocks(G, B, A)", 'has_keyword(A, "flying")', '!has_keyword(B, "flying")', '!has_keyword(B, "reach")'],
           note="§702.9b — a flyer is blockable only by flying/reach")
    p.rule("illegal_block(G, B, A)",
           ["blocks(G, B, A)", 'has_keyword(A, "menace")', "n_blockers(G, A, 1)"],
           note="§702.111b — menace needs two or more blockers")
    p.rule("illegal_block(G, B, A)",
           ["blocks(G, B, A)", 'has_keyword(A, "fear")', "!artifact(B)", "!black(B)"],
           note="§702.36b — fear: only artifact and/or black creatures may block")
    p.blank()

    # §702.20b vigilance — attacking taps a creature unless it has vigilance.
    p.decl("attacker_taps", [("g", "symbol"), ("c", "symbol")])
    p.rule("attacker_taps(G, C)", ["attacks(G, C, _)", '!has_keyword(C, "vigilance")'], note="§702.20b")
    p.blank()

    # §510.4 two-step combat damage. fs_source deals in the first-strike step;
    # reg_source deals in the regular step (a creature killed in the FS step deals nothing).
    p.decl("fs_source", [("c", "symbol")])
    p.rule("fs_source(C)", ['has_keyword(C, "first_strike")'])
    p.rule("fs_source(C)", ['has_keyword(C, "double_strike")'])
    p.decl("reg_source", [("c", "symbol")])
    p.rule("reg_source(C)", ['has_keyword(C, "double_strike")'], note="§702.4b double strike deals in both steps")
    p.rule("reg_source(C)", ["power(C, _)", '!has_keyword(C, "first_strike")', '!has_keyword(C, "double_strike")'])
    p.blank()
    # first-strike-step damage (blocked pairs + unblocked -> player).
    p.decl("fs_deals", [("g", "symbol"), ("s", "symbol"), ("t", "symbol"), ("n", "number")])
    p.rule("fs_deals(G, A, B, N)", ["blocks(G, B, A)", "fs_source(A)", "power(A, N)"])
    p.rule("fs_deals(G, B, A, N)", ["blocks(G, B, A)", "fs_source(B)", "power(B, N)"])
    p.rule("fs_deals(G, A, D, N)", ["attacks(G, A, D)", "is_player(D)", "!blocked(G, A)", "fs_source(A)", "power(A, N)"])
    p.decl("fs_lethal", [("g", "symbol"), ("c", "symbol")])
    p.rule("fs_lethal(G, C)", ["fs_deals(G, _, C, _)", "toughness(C, Tg)", "Tg > 0", "T = sum N : { fs_deals(G, _, C, N) }", "T >= Tg"])
    p.rule("fs_lethal(G, C)", ["fs_deals(G, S, C, N)", "N >= 1", 'has_keyword(S, "deathtouch")'])
    p.decl("fs_destroyed", [("g", "symbol"), ("c", "symbol")])
    p.rule("fs_destroyed(G, C)", ["fs_lethal(G, C)", '!has_keyword(C, "indestructible")'])
    p.blank()
    # regular-step damage — only from sources NOT destroyed in the first-strike step.
    p.decl("reg_deals", [("g", "symbol"), ("s", "symbol"), ("t", "symbol"), ("n", "number")])
    p.rule("reg_deals(G, A, B, N)", ["blocks(G, B, A)", "reg_source(A)", "!fs_destroyed(G, A)", "power(A, N)"])
    p.rule("reg_deals(G, B, A, N)", ["blocks(G, B, A)", "reg_source(B)", "!fs_destroyed(G, B)", "power(B, N)"])
    p.rule("reg_deals(G, A, D, N)", ["attacks(G, A, D)", "is_player(D)", "!blocked(G, A)", "reg_source(A)", "!fs_destroyed(G, A)", "power(A, N)"])
    p.blank()
    # total marked damage and lethality across both steps.
    p.decl("has_incoming", [("g", "symbol"), ("c", "symbol")])
    p.rule("has_incoming(G, C)", ["fs_deals(G, _, C, _)"])
    p.rule("has_incoming(G, C)", ["reg_deals(G, _, C, _)"])
    p.decl("marked", [("g", "symbol"), ("c", "symbol"), ("t", "number")])
    p.rule("marked(G, C, T)", ["has_incoming(G, C)",
                               "F = sum N : { fs_deals(G, _, C, N) }", "R = sum N : { reg_deals(G, _, C, N) }", "T = F + R"])
    p.decl("lethal", [("g", "symbol"), ("c", "symbol")])
    p.rule("lethal(G, C)", ["marked(G, C, T)", "toughness(C, Tg)", "Tg > 0", "T >= Tg"], note="§704.5g")
    p.rule("lethal(G, C)", ["fs_deals(G, S, C, N)", "N >= 1", 'has_keyword(S, "deathtouch")'], note="§702.2b")
    p.rule("lethal(G, C)", ["reg_deals(G, S, C, N)", "N >= 1", 'has_keyword(S, "deathtouch")'])
    p.decl("destroyed", [("g", "symbol"), ("c", "symbol")])
    p.rule("destroyed(G, C)", ["lethal(G, C)", '!has_keyword(C, "indestructible")'], note="§702.12b")
    p.blank()

    # §702.15b lifelink — a source's controller gains life equal to damage dealt.
    p.decl("ll_dealer", [("g", "symbol"), ("p", "symbol")])
    p.rule("ll_dealer(G, P)", ["fs_deals(G, S, _, _)", 'has_keyword(S, "lifelink")', "controls(P, S)"])
    p.rule("ll_dealer(G, P)", ["reg_deals(G, S, _, _)", 'has_keyword(S, "lifelink")', "controls(P, S)"])
    p.decl("life_gain", [("g", "symbol"), ("p", "symbol"), ("n", "number")])
    p.rule("life_gain(G, P, N)",
           ["ll_dealer(G, P)",
            'F = sum X : { fs_deals(G, S, _, X), has_keyword(S, "lifelink"), controls(P, S) }',
            'R = sum X : { reg_deals(G, S, _, X), has_keyword(S, "lifelink"), controls(P, S) }',
            "N = F + R"], note="§702.15b")
    p.blank()

    # §702.19 trample — excess over total blocker toughness to the defending player.
    p.decl("blocker_toughness", [("g", "symbol"), ("a", "symbol"), ("s", "number")])
    p.rule("blocker_toughness(G, A, S)", ["blocked(G, A)", "S = sum T : { blocks(G, B, A), toughness(B, T) }"])
    p.decl("trample_to_player", [("g", "symbol"), ("a", "symbol"), ("d", "symbol"), ("x", "number")])
    p.rule("trample_to_player(G, A, D, X)",
           ['has_keyword(A, "trample")', "attacks(G, A, D)", "is_player(D)", "blocked(G, A)",
            "power(A, P)", "blocker_toughness(G, A, S)", "X = P - S", "X > 0"], note="§702.19c")
    p.blank()

    # §702.80 wither / §702.90 infect — damage to a creature is dealt as -1/-1
    # counters; infect damage to a player is dealt as poison counters.
    p.decl("withering", [("s", "symbol")])
    p.rule("withering(S)", ['has_keyword(S, "wither")'])
    p.rule("withering(S)", ['has_keyword(S, "infect")'])
    p.decl("m1m1_target", [("g", "symbol"), ("c", "symbol")])
    p.rule("m1m1_target(G, C)", ["fs_deals(G, S, C, _)", "withering(S)", "toughness(C, _)"])
    p.rule("m1m1_target(G, C)", ["reg_deals(G, S, C, _)", "withering(S)", "toughness(C, _)"])
    p.decl("gives_m1m1", [("g", "symbol"), ("c", "symbol"), ("n", "number")])
    p.rule("gives_m1m1(G, C, N)",
           ["m1m1_target(G, C)",
            "F = sum X : { fs_deals(G, S, C, X), withering(S) }",
            "R = sum X : { reg_deals(G, S, C, X), withering(S) }", "N = F + R"],
           note="§702.80a / §702.90c — feeds the layer system as -1/-1 counters")
    p.decl("infect_player", [("g", "symbol"), ("p", "symbol")])
    p.rule("infect_player(G, P)", ["fs_deals(G, S, P, _)", 'has_keyword(S, "infect")', "is_player(P)"])
    p.rule("infect_player(G, P)", ["reg_deals(G, S, P, _)", 'has_keyword(S, "infect")', "is_player(P)"])
    p.decl("gives_poison", [("g", "symbol"), ("p", "symbol"), ("n", "number")])
    p.rule("gives_poison(G, P, N)",
           ["infect_player(G, P)",
            'F = sum X : { fs_deals(G, S, P, X), has_keyword(S, "infect") }',
            'R = sum X : { reg_deals(G, S, P, X), has_keyword(S, "infect") }', "N = F + R"],
           note="§702.90b — feeds the poison SBA (704.5c)")
    p.blank()

    p.output("cant_attack", "illegal_block", "attacker_taps", "destroyed",
             "trample_to_player", "life_gain", "gives_m1m1", "gives_poison")
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
    Path("datalog/keywords.dl").write_text(build(), encoding="utf-8")
    print("wrote datalog/keywords.dl")


if __name__ == "__main__":
    main()
