"""Build datalog/sba.dl by TRANSPILING §704.5 from rules.txt (no hand-written rules).

The rule bodies are produced by transpile.py (preprocess -> spaCy -> lark) from the
actual English text — not authored by hand. Covers the formulaic SBA sentences
(value conditions 704.5a/c/f/i + "ceases to exist" 704.5d). Reports coverage.
"""

from __future__ import annotations

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (run as script from interpreter/)

from pathlib import Path

from interpreter.dlgen import Program
from interpreter.rules_parser import split
from interpreter.transpile import transpile_rule
from interpreter.build_supertypes import extract as _supertypes_extract

INPUT_DECLS = [
    ("creature_perm", [("o", "symbol")]),
    ("planeswalker_perm", [("o", "symbol")]),
    ("is_token", [("o", "symbol")]),
    ("is_copy", [("o", "symbol")]),
    ("is_aura", [("o", "symbol")]), ("is_equipment", [("o", "symbol")]), ("is_battle", [("o", "symbol")]),
    ("illegally_attached", [("o", "symbol")]), ("attached", [("o", "symbol")]),
    ("has_supertype", [("o", "symbol"), ("sup", "symbol")]),     # legendary/world/snow/basic derived via §205.4
    ("world_since", [("o", "symbol"), ("ts", "number")]),        # when O got the world supertype, for 704.5k
    ("name", [("o", "symbol"), ("n", "symbol")]),    # for 704.5j legend rule
    ("controls", [("p", "symbol"), ("o", "symbol")]),
    ("commander", [("o", "symbol")]),                                                   # for 704.6c commander damage
    ("dealt_combat_damage", [("src", "symbol"), ("p", "symbol"), ("n", "number")]),
    ("in_zone", [("o", "symbol"), ("z", "symbol")]),
    ("is_scheme", [("o", "symbol")]), ("face_up", [("o", "symbol")]),          # for 704.6e schemes SBA
    ("scheme_trigger_pending", [("marker", "symbol")]),                         # a scheme's trigger still pending
    ("toughness", [("o", "symbol"), ("v", "number")]),
    ("marked", [("o", "symbol"), ("v", "number")]),             # damage marked, for 704.5g lethal
    ("dealt_deathtouch_damage", [("o", "symbol")]),             # for 704.5h
    ("loyalty", [("o", "symbol"), ("v", "number")]),
    ("life", [("p", "symbol"), ("v", "number")]),
    ("poison", [("p", "symbol"), ("v", "number")]),
]

EXPECT_DECLS = [
    ("expect_loses", [("p", "symbol"), ("rule", "symbol")]),
    ("expect_no_loses", [("p", "symbol")]),
    ("expect_graveyard", [("o", "symbol"), ("rule", "symbol")]),
    ("expect_no_graveyard", [("o", "symbol")]),
    ("expect_ceases", [("o", "symbol")]),
    ("expect_no_ceases", [("o", "symbol")]),
    ("expect_graveyard_attach", [("o", "symbol"), ("rule", "symbol")]),
    ("expect_unattach", [("o", "symbol"), ("rule", "symbol")]),
    ("expect_legend", [("o", "symbol")]), ("expect_no_legend", [("o", "symbol")]),
    ("expect_world", [("o", "symbol")]), ("expect_no_world", [("o", "symbol")]),
    ("expect_snow", [("o", "symbol")]), ("expect_basic", [("o", "symbol")]),
    ("expect_retire", [("o", "symbol")]), ("expect_no_retire", [("o", "symbol")]),
]
CHECKS = [
    ("loses", "expect_loses(P, R)", "miss", "sba_loses(P, R)"),
    ("no_loses", "expect_no_loses(P)", "hit", "sba_loses(P, _)"),
    ("graveyard", "expect_graveyard(O, R)", "miss", "sba_graveyard(O, R)"),
    ("no_graveyard", "expect_no_graveyard(O)", "hit", "sba_graveyard(O, _)"),
    ("ceases", "expect_ceases(O)", "miss", "sba_ceases(O)"),
    ("no_ceases", "expect_no_ceases(O)", "hit", "sba_ceases(O)"),
    ("gv_attach", "expect_graveyard_attach(O, R)", "miss", "sba_graveyard(O, R)"),
    ("unattach", "expect_unattach(O, R)", "miss", "sba_unattach(O, R)"),
    ("legend", "expect_legend(O)", "miss", "sba_legend_conflict(O)"),
    ("no_legend", "expect_no_legend(O)", "hit", "sba_legend_conflict(O)"),
    ("world", "expect_world(O)", "miss", "sba_world_conflict(O)"),
    ("no_world", "expect_no_world(O)", "hit", "sba_world_conflict(O)"),
    ("snow", "expect_snow(O)", "miss", "snow_permanent(O)"),
    ("basic", "expect_basic(O)", "miss", "basic_land(O)"),
    ("retire", "expect_retire(O)", "miss", "sba_scheme_retire(O)"),
    ("no_retire", "expect_no_retire(O)", "hit", "sba_scheme_retire(O)"),
]

SCENARIOS = [
    'life("alice", 0)', 'expect_loses("alice", "704.5a")',
    'life("bob", 5)', 'expect_no_loses("bob")',
    'poison("carol", 10)', 'expect_loses("carol", "704.5c")',
    'poison("dave", 9)', 'expect_no_loses("dave")',
    'creature_perm("zerotough")', 'toughness("zerotough", 0)', 'expect_graveyard("zerotough", "704.5f")',
    'creature_perm("healthy")', 'toughness("healthy", 3)', 'expect_no_graveyard("healthy")',
    'creature_perm("lethaled")', 'toughness("lethaled", 2)', 'marked("lethaled", 3)', 'expect_graveyard("lethaled", "704.5g")',
    'creature_perm("scratched")', 'toughness("scratched", 3)', 'marked("scratched", 1)', 'expect_no_graveyard("scratched")',
    'creature_perm("touched")', 'toughness("touched", 5)', 'dealt_deathtouch_damage("touched")', 'expect_graveyard("touched", "704.5h")',
    'planeswalker_perm("pw0")', 'loyalty("pw0", 0)', 'expect_graveyard("pw0", "704.5i")',
    'planeswalker_perm("pw5")', 'loyalty("pw5", 5)', 'expect_no_graveyard("pw5")',
    'is_token("tok_gy")', 'in_zone("tok_gy", "graveyard")', 'expect_ceases("tok_gy")',           # 704.5d
    'is_token("tok_bf")', 'in_zone("tok_bf", "battlefield")', 'expect_no_ceases("tok_bf")',
    'is_copy("cp_gy")', 'in_zone("cp_gy", "graveyard")', 'expect_ceases("cp_gy")',               # 704.5e
    'is_copy("cp_stack")', 'in_zone("cp_stack", "stack")', 'expect_no_ceases("cp_stack")',
    'life("team_dead", 0)', 'expect_loses("team_dead", "704.6a")',                               # 704.6a (Two-Headed Giant)
    'poison("team_toxic", 15)', 'expect_loses("team_toxic", "704.6b")',                          # 704.6b
    'is_aura("bad_aura")', 'illegally_attached("bad_aura")', 'expect_graveyard_attach("bad_aura", "704.5m")',
    'is_equipment("bad_equip")', 'illegally_attached("bad_equip")', 'expect_unattach("bad_equip", "704.5n")',
    'is_battle("attached_battle")', 'attached("attached_battle")', 'expect_unattach("attached_battle", "704.5p")',
    # 704.5j legend rule — two legendary "Tarmogoyf" controlled by alice conflict; a lone legend doesn't.
    # legendary is now DERIVED from the "legendary" supertype via the interpreted §205.4 supertype_rule.
    'has_supertype("goyf1", "legendary")', 'name("goyf1", "Tarmogoyf")', 'controls("alice", "goyf1")',
    'has_supertype("goyf2", "legendary")', 'name("goyf2", "Tarmogoyf")', 'controls("alice", "goyf2")',
    'expect_legend("goyf1")', 'expect_legend("goyf2")',
    'has_supertype("solo", "legendary")', 'name("solo", "Jace")', 'controls("alice", "solo")', 'expect_no_legend("solo")',
    # 704.5k world rule (one global pool) — newest world permanent (max world_since) survives; older dies.
    'has_supertype("nether", "world")', 'world_since("nether", 1)', 'expect_world("nether")',     # older -> dies
    'has_supertype("arena", "world")', 'world_since("arena", 3)', 'expect_no_world("arena")',      # newest -> survives
    # §205.4 classifications — supertype "snow"/"basic" confer snow_permanent / basic_land.
    'has_supertype("rime", "snow")', 'expect_snow("rime")',
    'has_supertype("tundra", "basic")', 'expect_basic("tundra")',
    # 704.6e schemes SBA — a face-up non-ongoing scheme in the command zone is retired...
    'is_scheme("plot")', 'face_up("plot")', 'in_zone("plot", "command")', 'expect_retire("plot")',
    # ...but an ongoing scheme is exempt (§205.4h, derived scheme_exempt), so it stays.
    'is_scheme("eternal")', 'face_up("eternal")', 'in_zone("eternal", "command")',
    'has_supertype("eternal", "ongoing")', 'expect_no_retire("eternal")',
    # 704.6c commander damage — voltron has dealt victim 21 combat damage (summed), so victim loses.
    'commander("voltron")', 'dealt_combat_damage("voltron", "victim", 13)', 'dealt_combat_damage("voltron", "victim", 8)',
    'expect_loses("victim", "704.6c")',
    'commander("smallcmd")', 'dealt_combat_damage("smallcmd", "safe", 10)', 'expect_no_loses("safe")',
]


def transpile_704_5() -> tuple[list, list]:
    doc = split(Path("rules.txt").read_text(encoding="utf-8"))
    transpiled, skipped = [], []
    for s in doc.sections:
        for g in s.groups:
            if g.number == "704":
                for r in g.rules:
                    for sr in r.subrules:
                        if not sr.number.startswith(("704.5", "704.6")):   # 704.5 player SBAs + 704.6 team/variant SBAs
                            continue
                        out = transpile_rule(sr.number, sr.text)
                        # Keep ONLY genuine SBA outputs (sba_* head). The generic cross-cutting
                        # patterns (conditional/relation/…) now also match some §704.5/6 subrules,
                        # but those facts belong in their own .dl — emitting them here would inject
                        # relations sba.dl never declares (e.g. conditional), breaking compilation.
                        if out and out.datalog.lstrip().startswith("sba_"):
                            transpiled.append((sr.number, out.datalog))
                        else:
                            skipped.append(sr.number)
    return transpiled, skipped


def build() -> tuple[str, dict]:
    transpiled, skipped = transpile_704_5()
    p = Program()
    p.comment("sba.dl — §704.5 state-based actions TRANSPILED from rules.txt by transpile.py")
    p.comment("(preprocess -> spaCy dependency parse -> lark). Rule bodies are GENERATED, not hand-written.")
    p.blank()
    for name, cols in INPUT_DECLS:
        p.decl(name, cols)
    p.decl("sba_loses", [("p", "symbol"), ("rule", "symbol")])
    p.decl("sba_graveyard", [("o", "symbol"), ("rule", "symbol")])
    p.decl("sba_ceases", [("o", "symbol")])
    p.decl("sba_unattach", [("o", "symbol"), ("rule", "symbol")])
    p.decl("sba_legend_conflict", [("o", "symbol")])
    p.blank()
    p.comment("§205.4 supertype semantics, INTERPRETED from rules.txt by build_supertypes.")
    p.comment("legendary = a permanent whose supertype the rules subject to the legend rule (§704.5j).")
    p.decl("supertype_rule", [("supertype", "symbol"), ("subject", "symbol"), ("rule", "symbol")])
    p.facts(list(dict.fromkeys(
        f'supertype_rule("{sup}", "{subj}", "{rule}")' for _n, sup, subj, rule in _supertypes_extract())))
    p.comment("each supertype's consequence, derived the same way: read off the interpreted §205.4 rule.")
    p.decl("legendary", [("o", "symbol")])
    p.rule("legendary(O)", ["has_supertype(O, Sup)", 'supertype_rule(Sup, "permanent", "legend_rule")'])
    p.decl("world_perm", [("o", "symbol")])
    p.rule("world_perm(O)", ["has_supertype(O, Sup)", 'supertype_rule(Sup, "permanent", "world_rule")'])
    p.decl("snow_permanent", [("o", "symbol")])
    p.rule("snow_permanent(O)", ["has_supertype(O, Sup)", 'supertype_rule(Sup, "permanent", "snow_permanent")'])
    p.decl("basic_land", [("o", "symbol")])
    p.rule("basic_land(O)", ["has_supertype(O, Sup)", 'supertype_rule(Sup, "land", "basic_land")'])
    p.decl("scheme_exempt", [("o", "symbol")])
    p.rule("scheme_exempt(O)", ["has_supertype(O, Sup)", 'supertype_rule(Sup, "scheme", "schemes_sba_exempt")'])
    p.decl("sba_world_conflict", [("o", "symbol")])
    p.decl("sba_scheme_retire", [("o", "symbol")])
    p.blank()
    p.comment("--- rules transpiled from the English text (one per matched §704.5 sentence) ---")
    for _num, dl in transpiled:
        p.raw(dl)
    p.blank()
    p.output("sba_loses", "sba_graveyard", "sba_ceases", "sba_unattach", "sba_legend_conflict")
    p.output("sba_world_conflict", "snow_permanent", "basic_land", "scheme_exempt", "sba_scheme_retire")
    p.blank()
    p.comment("conformance")
    p.conformance(EXPECT_DECLS, CHECKS)
    p.blank()
    p.comment("scenario under test")
    for atom in SCENARIOS:
        p.fact(atom)
    return p.text(), {"transpiled": [n for n, _ in transpiled], "skipped_count": len(skipped)}


def main() -> None:
    Path("datalog").mkdir(exist_ok=True)
    source, report = build()
    Path("datalog/sba.dl").write_text(source, encoding="utf-8")
    print("wrote datalog/sba.dl")
    print(f"  transpiled mechanically: {report['transpiled']}")
    print(f"  could NOT transpile:     {report['skipped_count']} subrules")


if __name__ == "__main__":
    main()
