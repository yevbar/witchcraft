"""test_migrate_endure.py — '<creature> endures N' (§701 endure, Bloomburrow/Duskmourn 2024) onto the lark AST.

Endure is a §701 keyword action, but unlike the verb-first numbered actions (bolster/discover -> you-actor) it's a
CREATURE action: the SUBJECT is the actor/target — 'it endures 2' -> endure(2, it). So it gets its own subject-
prefixed production (enduresubj ENDURE endurenum) whose transformer re-applies _endure's EXACT regex to self._src
-> endure(<n|->, _target(subj)). All 5 'it endures N' corpus clauses ground byte-identically (DIFFERS=0). The
dynamic 'it endures X, where X is …' and compound 'if you do, it endures N' forms are different shapes, left on regex.

Run: MTG_NO_SPACY=1 python3 test_migrate_endure.py
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)
from interpreter.card_effects import parse_clause
from interpreter.card_lark import parse_clause_lark
CH=[]
def ck(n,c): CH.append((n,bool(c)))
def tp(e): return (e.verb,str(e.amount),e.target,e.extra,e.cond) if e else None
def run():
    for s,tgt,amt in [("it endures 2","it","2"),("it endures 3","it","3"),
                      ("target creature endures 2","target_creature","2"),
                      ("~ endures 2","self","2"),("it endures x","it","X")]:
        ck(f"lark grounds {s!r} -> endure({amt}, {tgt})", tp(parse_clause_lark(s))==("endure",amt,tgt,"-","-"))
        ck(f"byte-identical to regex for {s!r}", tp(parse_clause_lark(s))==tp(parse_clause(s)))
    # guards: the subject is required (bare 'endure N' isn't this creature-action form); other kw actions unchanged
    ck("bare 'endure 2' (no subject) NOT grounded by endureclause", parse_clause_lark("endure 2") is None)
    ck("'it endures all the time' (non-count tail) abstains", parse_clause_lark("it endures all the time") is None)
    ck("'discover 3' unchanged", tp(parse_clause_lark("discover 3"))==("discover","3","you","-","-"))
    ck("'bolster 2' unchanged", tp(parse_clause_lark("bolster 2"))==("bolster","2","you","-","-"))
    p=sum(1 for _,o in CH if o)
    for n,o in CH: print(f"  {'ok  ' if o else 'FAIL'} {n}")
    print(f"\n{p}/{len(CH)} checks passed")
    if p!=len(CH): raise SystemExit(1)
if __name__=="__main__": run()
