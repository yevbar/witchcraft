"""test_migrate_becomes_blocked.py — NET-NEW: '<subj> becomes blocked' (§509 forced-block state change).

'Target unblocked attacking creature becomes blocked' (~5 cards) was uncovered (parse_clause=None). It has the
same shape as the §701 status designations 'becomes foretold/plotted' already grounded by the BECOMESDESIG
terminal + bcdesig_v -> becomes(-, _target(subj), <state>), so 'blocked' was added to that closed terminal list.
The 'becomes blocked' bigram is specific (it can't match 'unblocked' — needs 'becomes ' before 'blocked' — nor the
'must be blocked' / "can't be blocked" families, which use 'be blocked'). Before/after over block/becomes clauses:
3 distinct newly grounded, 0 lost, 0 changed.

Run: MTG_NO_SPACY=1 python3 test_migrate_becomes_blocked.py
"""
from card_effects import parse_clause
from card_lark import parse_clause_lark
CH=[]
def ck(n,c): CH.append((n,bool(c)))
def tp(e): return (e.verb,str(e.amount),e.target,e.extra,e.cond) if e else None
def run():
    for s,tgt in [("target unblocked attacking creature becomes blocked","target_unblocked_attacking_creature"),
                  ("attacking creatures become blocked","attacking_creatures"),
                  ("x target attacking creatures become blocked","x_target_attacking_creatures")]:
        ck(f"lark grounds {s[:40]!r} -> becomes({tgt}, blocked)",
           tp(parse_clause_lark(s))==("becomes","-",tgt,"blocked","-"))
        ck(f"parse_clause end-to-end {s[:30]!r}", tp(parse_clause(s))==("becomes","-",tgt,"blocked","-"))
    # guards: the existing status designations + the block-RESTRICTION families are unchanged
    ck("'~ becomes foretold' unchanged", tp(parse_clause_lark("~ becomes foretold"))==("becomes","-","self","foretold","-"))
    ck("'it becomes plotted' unchanged", tp(parse_clause_lark("it becomes plotted"))==("becomes","-","it","plotted","-"))
    ck("\"~ can't be blocked\" still cant_be_blocked",
       (lambda e: e is not None and e.verb=="cant_be_blocked")(parse_clause_lark("~ can't be blocked")))
    ck("'~ must be blocked if able' still must_be_blocked",
       (lambda e: e is not None and e.verb=="must_be_blocked")(parse_clause_lark("~ must be blocked if able")))
    ck("'unblocked' not mis-lexed: 'target unblocked attacking creature gets +1/+1' -> modify_pt",
       (lambda e: e is not None and e.verb=="modify_pt")(parse_clause_lark("target unblocked attacking creature gets +1/+1")))
    p=sum(1 for _,o in CH if o)
    for n,o in CH: print(f"  {'ok  ' if o else 'FAIL'} {n}")
    print(f"\n{p}/{len(CH)} checks passed")
    if p!=len(CH): raise SystemExit(1)
if __name__=="__main__": run()
