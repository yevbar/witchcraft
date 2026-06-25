"""test_migrate_becomes_subtype.py — NET-NEW: '<subj> becomes a/an <subtype> [until eot]' (§205 type set).

'Target creature becomes a Coward', 'Enchanted creature is a Flagbearer', 'it becomes a Demon Spirit' (~22 cards)
were uncovered: the closed card-type list (_BCT) only covers CARD types (artifact/creature/land/…), not creature
SUBTYPES (Coward/Warrior/Flagbearer/…). Added a bctype_v fallback (LAST, after card-type/color-type/in-addition)
that grounds becomes(-, _target(subj), slug(subtype)) IFF every word of the type phrase is a grounded §205 subtype
(ground.permanent_subtypes, read from enum_member facts in enumerations.dl — the proper grounded source, not
corpus-derived). Gating means a non-subtype ('a black Zombie' -> color, handled earlier by _BCCT; junk) abstains
instead of slugging garbage. Transformer-only (no grammar change -> no shadow). Before/after over becomes clauses:
10 distinct newly grounded, 0 lost, 0 changed.

Run: MTG_NO_SPACY=1 python3 test_migrate_becomes_subtype.py
"""
from card_effects import parse_clause
from card_lark import parse_clause_lark
import ground
CH=[]
def ck(n,c): CH.append((n,bool(c)))
def tp(e): return (e.verb,str(e.amount),e.target,e.extra,e.cond) if e else None
def run():
    cases=[("target creature becomes a coward until end of turn","target_creature","coward"),
           ("enchanted creature is a flagbearer","enchanted_creature","flagbearer"),
           ("target creature becomes a warrior until end of turn","target_creature","warrior"),
           ("enchanted creature is a demon spirit","enchanted_creature","demon_spirit"),
           ("target creature becomes a zombie","target_creature","zombie"),
           ("it becomes a human cleric","it","human_cleric")]
    for s,tgt,sub in cases:
        ck(f"lark grounds {s[:38]!r} -> becomes({tgt}, {sub})",
           tp(parse_clause_lark(s))==("becomes","-",tgt,sub,"-"))
        ck(f"parse_clause end-to-end {s[:30]!r}", tp(parse_clause(s))==("becomes","-",tgt,sub,"-"))
    # multi-word subtype validated per word
    ck("multi-word subtype 'God Warrior Hero' grounds (all 3 are creature types)",
       tp(parse_clause_lark("he becomes a god warrior hero"))==("becomes","-","self","god_warrior_hero","-"))
    # the validator is grounded from the §205 enumeration
    ck("ground.permanent_subtypes contains coward/treasure, excludes 'black'",
       "coward" in ground.permanent_subtypes() and "treasure" in ground.permanent_subtypes()
       and "black" not in ground.permanent_subtypes())
    # GUARDS — precedence preserved; non-subtype words abstain (no garbage)
    ck("'is a black zombie' still color-type (black_zombie)",
       tp(parse_clause_lark("enchanted creature is a black zombie"))==("becomes","-","enchanted_creature","black_zombie","-"))
    ck("'becomes an artifact' still card-type",
       tp(parse_clause_lark("enchanted creature becomes an artifact"))==("becomes","-","enchanted_creature","artifact","-"))
    ck("'becomes a coward in addition to its other types' still added_ (in-addition)",
       tp(parse_clause_lark("target creature becomes a coward in addition to its other types"))==("becomes","-","target_creature","added_coward","-"))
    ck("'becomes a copy of target creature' still copy",
       (lambda e: e is not None and "copy" in e.extra)(parse_clause_lark("enchanted creature becomes a copy of target creature")))
    ck("'becomes monstrous' (not a subtype, no article) abstains", parse_clause_lark("it becomes monstrous") is None)
    ck("'becomes a 4/4 creature' still base-pt", str(parse_clause_lark("target creature becomes a 4/4 creature").amount)=="4/4")
    ck("'becomes a glorber' (junk subtype) abstains", parse_clause_lark("target creature becomes a glorber") is None)
    p=sum(1 for _,o in CH if o)
    for n,o in CH: print(f"  {'ok  ' if o else 'FAIL'} {n}")
    print(f"\n{p}/{len(CH)} checks passed")
    if p!=len(CH): raise SystemExit(1)
if __name__=="__main__": run()
