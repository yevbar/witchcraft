import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (run as script from interpreter/)

from interpreter import card_corpus, ground
import collections, re
from interpreter.transpile_card import (transpile_unit, _TRIG, _split_modifiers, _sentences, _mask_q, _unmask,
                            _smart_split, _is_compound_object, _leading_subject, _has_leading_subject)
from interpreter.card_effects import parse_clause, parse_clauses
_INT=re.compile(r"\b\d+\b"); _SYM=re.compile(r"\{[^}]+\}")
def tmpl(s): return _INT.sub("N",_SYM.sub("{S}",s)).strip()
def failparts(sentence):
    # mirror _parse_body exactly (smart split + subject propagation)
    multi=parse_clauses(sentence)
    if multi and (len(multi)>1 or not _is_compound_object(sentence)): return []
    masked,q=_mask_q(sentence)
    masked=re.sub(r"power and toughness","power\x00and\x00toughness",masked,flags=re.I)
    parts=[_unmask(p.replace("\x00"," "),q) for p in _smart_split(masked)]
    bad=[]
    if len(parts)>=2:
        subj=_leading_subject(parts[0])
        for j,p in enumerate(parts):
            e=None
            if j>0 and subj and not _has_leading_subject(p): e=parse_clause(f"{subj} {p}")
            if not e: e=parse_clause(p)
            if not e: bad.append(p)
        if not bad: return []
        return bad
    return [sentence] if not multi else []
fails=collections.Counter()
for c in card_corpus.load_cards():
    units=card_corpus.units_of(c)
    if not units: continue
    cid=ground.slug(c['name'])
    fu=[u for i,u in enumerate(units) if not transpile_unit(u,{'id':cid,'card':c,'seq':i})]
    if len(fu)!=1: continue
    raw=fu[0].raw
    m=_TRIG.match(raw)
    if m: raw=m.group('body')
    else:
        cm=re.match(r'^([^:]{1,60}):\s*(.+)$',raw)
        if cm and '{' in cm.group(1): raw=cm.group(2)
    body,_=_split_modifiers(raw)
    for sent in _sentences(body):
        sent=sent.rstrip('.')
        if not sent: continue
        for bad in failparts(sent):
            fails[tmpl(bad)]+=1
print("=== true atomic blockers (single-blocker cards) ===")
for t,n in fails.most_common(40): print(f'  {n:4}  {t[:72]}')
