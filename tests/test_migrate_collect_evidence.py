"""test_migrate_collect_evidence.py — 'collect evidence N' (§701, Murders at Karlov Manor 2024) onto the lark AST.

Collect evidence is a multi-word §701 keyword action. Bare 'collect evidence' already grounds (the KV_MULTI
whole-phrase terminal + kvmulti -> collect_evidence(-, you)). The NUMBERED form 'collect evidence N' (the only
numbered multi-word keyword action) abstained: KV_MULTI matched the phrase but the trailing count wasn't consumed.
Made the kvmclause count OPTIONAL (KV_MULTI kvmnum?) and extended kvmulti to apply the EXACT `_kwaction_n`
template when a count is present -> collect_evidence(<N>, you) (count restricted to the numeric set; verb
re-validated vs keyword_actions). Byte-identical, DIFFERS=0. In the corpus these appear may-wrapped ('you may
collect evidence N') — parse_clause peels 'you may' (cond=may) and the inner now grounds via lark.

Run: MTG_NO_SPACY=1 python3 test_migrate_collect_evidence.py
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)
from interpreter.card_effects import parse_clause
from interpreter.card_lark import parse_clause_lark
CH=[]
def ck(n,c): CH.append((n,bool(c)))
def tp(e): return (e.verb,str(e.amount),e.target,e.extra,e.cond) if e else None
def run():
    for n in ("3","6","8","10"):
        s=f"collect evidence {n}"
        ck(f"lark grounds {s!r} -> collect_evidence({n}, you)", tp(parse_clause_lark(s))==("collect_evidence",n,"you","-","-"))
        ck(f"byte-identical to regex for {s!r}", tp(parse_clause_lark(s))==tp(parse_clause(s)))
    # bare 'collect evidence' (no count) still grounds unchanged
    ck("bare 'collect evidence' -> collect_evidence(-, you)",
       tp(parse_clause_lark("collect evidence"))==("collect_evidence","-","you","-","-"))
    # other multi-word keyword actions are UNCHANGED by the optional count
    ck("'manifest dread' unchanged", tp(parse_clause_lark("manifest dread"))==("manifest_dread","-","you","-","-"))
    ck("'time travel' unchanged", tp(parse_clause_lark("time travel"))==("time_travel","-","you","-","-"))
    ck("'venture into the dungeon' unchanged",
       tp(parse_clause_lark("venture into the dungeon"))==("venture_into_the_dungeon","-","you","-","-"))
    # the may-wrapped corpus form grounds end-to-end (parse_clause peels 'you may' -> cond=may; inner via lark)
    ck("'you may collect evidence 4' -> collect_evidence(4, you, may)",
       tp(parse_clause("you may collect evidence 4"))==("collect_evidence","4","you","-","may"))
    p=sum(1 for _,o in CH if o)
    for n,o in CH: print(f"  {'ok  ' if o else 'FAIL'} {n}")
    print(f"\n{p}/{len(CH)} checks passed")
    if p!=len(CH): raise SystemExit(1)
if __name__=="__main__": run()
