"""test_migrate_discover.py — 'discover N' (§701, Lost Caverns of Ixalan 2024) migrated onto the lark AST.

Discover is a numbered §701 keyword action, grounded discover(<N>, you) — the same shape as bolster/adapt/
incubate/amass that the kwnclause (KWACTION_N + count) production owns. 'discover' was simply missing from the
KWACTION_N terminal list, so 'discover N' abstained in lark and fell to the regex leaf. Adding it (FLIP-ONLY; the
kwaction_n transformer re-validates the verb against ground.keyword_actions and the count against the template's
numeric set) grounds all 9 bare 'discover N' corpus clauses byte-identically. (The 'discover X, where X is …'
dynamic-amount and 'if you do, discover N' compound forms are different shapes and stay on the regex leaf.)

Run: MTG_NO_SPACY=1 python3 test_migrate_discover.py
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)
from card_effects import parse_clause
from card_lark import parse_clause_lark
CH=[]
def ck(n,c): CH.append((n,bool(c)))
def tp(e): return (e.verb,str(e.amount),e.target,e.extra,e.cond) if e else None
def run():
    for n in ("3","4","5","10"):
        s=f"discover {n}"
        ck(f"lark grounds {s!r} -> discover({n}, you)", tp(parse_clause_lark(s))==("discover",n,"you","-","-"))
        ck(f"byte-identical to regex for {s!r}", tp(parse_clause_lark(s))==tp(parse_clause(s)))
    # guards: other numbered keyword actions unchanged; bare/near-miss words abstain
    ck("'bolster 2' unchanged", tp(parse_clause_lark("bolster 2"))==("bolster","2","you","-","-"))
    ck("'amass orcs 1' unchanged", tp(parse_clause_lark("amass orcs 1"))==("amass","1","you","orcs","-"))
    ck("bare 'discover' (no count) abstains", parse_clause_lark("discover") is None)
    ck("'discovery' (different word) abstains", parse_clause_lark("discovery") is None)
    p=sum(1 for _,o in CH if o)
    for n,o in CH: print(f"  {'ok  ' if o else 'FAIL'} {n}")
    print(f"\n{p}/{len(CH)} checks passed")
    if p!=len(CH): raise SystemExit(1)
if __name__=="__main__": run()
