"""§122/§614 'enters with N counters' — widen the SELF subject/object beyond 'it'.

Recent character cards use gendered self-pronouns ('on him'/'on her') and self-reference by the legendary
SHORT name ('Hulk enters …' on 'Hulk, Strongest There Is', which the corpus leaves un-masked). The short
name is read from THIS card's own name (ctx), so it's scoped and faithful — no corpus-wide name masking.
'twice X' / 'half X' counts and non-+N/+N counter kinds (shield, hexproof) are kept.
"""
import os
os.environ.setdefault("MTG_NO_SPACY", "1")

import card_corpus
import ground
from transpile_card import transpile_unit


def _fact(name, text):
    c = {"name": name, "text": text}
    cid = ground.slug(name)
    o = transpile_unit(card_corpus.units_of(c)[0], {"id": cid, "card": c, "seq": 0})
    return o.facts[0] if o else None


def test_gendered_self_pronoun():
    assert _fact("Hulk, Strongest There Is", "Hulk enters with a +1/+1 counter on him.") == \
        'enters_with_counters("hulk_strongest_there_is", "1_1", "a")'
    assert _fact("Big Bertha", "~ enters with X +1/+1 counters on her.") == \
        'enters_with_counters("big_bertha", "1_1", "x")'


def test_short_name_subject():
    assert _fact("The Duke, Rebel Sentry", "The Duke enters with a +1/+1 counter on him.") == \
        'enters_with_counters("the_duke_rebel_sentry", "1_1", "a")'


def test_non_pt_counter_kind():
    assert _fact("Captain America, Super-Soldier", "Captain America enters with a shield counter on him.") == \
        'enters_with_counters("captain_america_super_soldier", "shield", "a")'


def test_twice_x_count():
    assert _fact("Primo, the Unbounded", "Primo enters with twice X +1/+1 counters on it.") == \
        'enters_with_counters("primo_the_unbounded", "1_1", "twice_x")'


def test_canonical_it_form_unchanged():
    assert _fact("X", "~ enters with a +1/+1 counter on it.") == \
        'enters_with_counters("x", "1_1", "a")'
    assert _fact("X", "~ enters with two +1/+1 counters on it.") == \
        'enters_with_counters("x", "1_1", "two")'


if __name__ == "__main__":
    test_gendered_self_pronoun()
    test_short_name_subject()
    test_non_pt_counter_kind()
    test_twice_x_count()
    test_canonical_it_form_unchanged()
    print("ok")
