"""Token-creation 'plus' replacement (§614, token sibling of the Hardened Scales counter family).

'If <…tokens> would be created …, those tokens plus <SPEC> are created instead' (and the 'instead create
those tokens plus <SPEC>' phrasing). Faithful — the additional token <SPEC> is parsed as a real create
effect on a 'replacement' ability (not a lossy doubles_tokens-style flag); 'an additional Map token'
normalizes to a clean 'map' token slug.
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
        out.append((o.pattern, list(o.facts)) if o else None)
    return out


def test_phrasing_a_typed_token():
    f = _facts("Quina, Qu Gourmet",
               "If one or more tokens would be created under your control, "
               "those tokens plus a 1/1 green Frog creature token are created instead.")
    assert f[0] is not None and f[0][0] == "replacement"
    facts = f[0][1]
    assert 'card_ability("quina_qu_gourmet", "a0", "replacement")' in facts
    assert any('"create", "1", "token", "1_1_green_frog_creature"' in x for x in facts)


def test_phrasing_b_additional_normalized():
    # 'instead create those tokens plus an additional Map token' -> clean 'map' slug
    f = _facts("Worldwalker Helm",
               "If you would create one or more artifact tokens, "
               "instead create those tokens plus an additional Map token.")
    assert f[0] is not None
    assert any('"create", "1", "token", "map"' in x for x in f[0][1])


def test_predefined_token_normalizes():
    f = _facts("Xorn", "If you would create one or more Treasure tokens, "
               "instead create those tokens plus an additional Treasure token.")
    assert any('"create", "1", "token", "treasure"' in x for x in f[0][1])


if __name__ == "__main__":
    test_phrasing_a_typed_token()
    test_phrasing_b_additional_normalized()
    test_predefined_token_normalizes()
    print("ok")
