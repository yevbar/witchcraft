"""Hardened Scales family: 'that many plus one' counter replacement -> static(counters_plus_one).

The §614 counter-doubling effects ('twice that many') already ground as static(doubles_counters); their
'+1' siblings (Hardened Scales / Conclave Mentor / Winding Constrictor — all printed as 'that many plus one')
fell through. Added the parallel descriptive static. Other phrasings ('you get ... that many plus one',
'a modular ability would put …') are genuinely different shapes and stay unmatched.
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)
import os
os.environ.setdefault("MTG_NO_SPACY", "1")

import card_corpus
import ground
from transpile_card import transpile_unit


def _facts(name, text):
    c = {"name": name, "text": text}
    cid = ground.slug(name)
    out = []
    for i, u in enumerate(card_corpus.units_of(c)):
        o = transpile_unit(u, {"id": cid, "card": c, "seq": i})
        out.append(o.facts if o else None)
    return out


def test_hardened_scales_grounds():
    f = _facts("Hardened Scales",
               "If one or more +1/+1 counters would be put on a creature you control, "
               "that many plus one +1/+1 counters are put instead.")
    assert f == [[f'static("hardened_scales", "counters_plus_one")']]


def test_any_counter_and_alt_phrasing():
    # 'one or more counters' (no +1/+1 qualifier) — Winding Constrictor's put-on form
    assert _facts("X", "If one or more counters would be put on an artifact or creature you control, "
                  "that many plus one of each of those kinds of counters are put instead.") == \
        [[f'static("x", "counters_plus_one")']]
    # 'If you would put …, put that many plus one … instead' — Lae'zel's phrasing
    assert _facts("Y", "If you would put one or more counters on a creature or planeswalker you control, "
                  "put that many plus one of each of those kinds of counters on it instead.") == \
        [[f'static("y", "counters_plus_one")']]


def test_doubling_still_doubles():
    # the 'twice that many' sibling must keep its own slug, not collapse into plus_one
    assert _facts("Z", "If one or more +1/+1 counters would be put on a creature you control, "
                  "twice that many +1/+1 counters are put instead.") == \
        [[f'static("z", "doubles_counters")']]


if __name__ == "__main__":
    test_hardened_scales_grounds()
    test_any_counter_and_alt_phrasing()
    test_doubling_still_doubles()
    print("ok")
