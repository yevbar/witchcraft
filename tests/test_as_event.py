"""test_as_event.py — the _as_event transpile pattern: §603.6/§614.12 NON-enters as-events.

'As <subj> <event>, <effect>' for the as-events _as_enters (which only matches '~/it enters') doesn't reach:
'is turned face up' (morph/disguise flip), 'becomes attached to …' (aura/equipment), 'transforms into …',
'becomes monstrous' — grounded as a triggered ability on the event (body must ground, else abstain). Runs after
_as_enters; 'enters' is deliberately excluded so the dedicated etb_choose grounding for 'As <Name> enters,
choose <X>' is preserved. Full before/after over all 'As ' units: 6 newly grounded, 0 lost, 0 changed.

Run: MTG_NO_SPACY=1 python3 test_as_event.py
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)
from build_cards import transpile_unit
import card_corpus
CH=[]
def ck(n,c): CH.append((n,bool(c)))

def facts_for(cardname, needle):
    for c in card_corpus.load_cards():
        if c["name"] != cardname:
            continue
        for seq,u in enumerate(card_corpus.units_of(c)):
            if needle.lower() in u.raw.lower():
                o = transpile_unit(u, {"id":"t","card":c,"seq":seq})
                return o.facts if o else None
    return "CARD/UNIT NOT FOUND"

def run():
    # NEW coverage: the non-enters as-events ground as triggered abilities on the event
    sb = facts_for("Sanctuary Blade", "becomes attached")
    ck("Sanctuary Blade 'As ~ becomes attached…, choose a color' -> triggered/becomes_attached/choose(color)",
       sb and any('ability_trigger' in f and 'becomes_attached' in f for f in sb)
       and any('card_effect' in f and 'choose' in f and 'color' in f for f in sb))
    bs = facts_for("Bubble Smuggler", "turned face up")
    ck("Bubble Smuggler 'As ~ is turned face up, put four +1/+1 counters' -> triggered/turned_face_up",
       bs and any('ability_trigger' in f and 'turned_face_up' in f for f in bs)
       and any('put_counter' in f for f in bs))
    sh = facts_for("Shinryu, Transcendent Rival", "transforms into")
    ck("Shinryu 'As ~ transforms into …, choose an opponent' -> triggered/transforms",
       sh and any('ability_trigger' in f and 'transforms' in f for f in sh))

    # GUARD: 'As <Name> enters, choose <X>' KEEPS its dedicated etb_choose grounding (NOT stolen by _as_event)
    morophon = facts_for("Morophon, the Boundless", "As Morophon enters")
    ck("Morophon 'As Morophon enters, choose a creature type' still -> etb_choose(creature_type)",
       morophon and any(f.startswith('etb_choose') and 'creature_type' in f for f in morophon))
    iona = facts_for("Iona, Shield of Emeria", "As Iona enters")
    ck("Iona 'As Iona enters, choose a color' still -> etb_choose(color)",
       iona and any(f.startswith('etb_choose') and 'color' in f for f in iona))

    p=sum(1 for _,o in CH if o)
    for n,o in CH: print(f"  {'ok  ' if o else 'FAIL'} {n}")
    print(f"\n{p}/{len(CH)} checks passed")
    if p!=len(CH): raise SystemExit(1)
if __name__=="__main__": run()
