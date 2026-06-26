"""§118 casting-permission static — widen the leading frequency/timing peel to include 'Until …' durations.

The permission handler peels a leading 'Once during each of your turns,' / 'During your turn,' off a
'you may cast/play <X> from <zone>' static and records it on the slug. The impulse-draw payoff
('Exile the top N cards. Until end of turn, you may cast spells from among them.') uses a leading DURATION
instead ('Until end of turn,' / 'Until your next end step,' / 'Until the end of your next turn,'), which was
not peeled. Adding those durations grounds the permission with the duration on the slug. Existing
frequency forms are unchanged.
"""
import os
os.environ.setdefault("MTG_NO_SPACY", "1")

import card_corpus
import ground
from transpile_card import transpile_unit


def _fact(text, name="T"):
    c = {"name": name, "text": text}
    cid = ground.slug(name)
    o = transpile_unit(card_corpus.units_of(c)[0], {"id": cid, "card": c, "seq": 0})
    return o.facts[0] if o else None


def test_until_end_of_turn():
    assert _fact("Until end of turn, you may cast spells from among them.") == \
        'static_player("t", "may_cast_spells_from_among_them_until_end_of_turn")'


def test_until_next_end_step():
    assert _fact("Until your next end step, you may cast a spell from among them.") == \
        'static_player("t", "may_cast_a_spell_from_among_them_until_your_next_end_step")'


def test_multi_sentence_impulse():
    # the real impulse-draw shape: exile then a duration-gated permission, grounded via _multi_sentence
    f = _fact("Exile the top three cards of your library. Until end of turn, you may cast spells from among them.")
    assert f is not None


def test_existing_frequency_unchanged():
    assert _fact("Once during each of your turns, you may cast a permanent spell from your graveyard.") == \
        'static_player("t", "may_cast_a_permanent_spell_from_your_graveyard_once_per_turn")'
    assert _fact("During your turn, you may play lands from your graveyard.") == \
        'static_player("t", "may_play_lands_from_your_graveyard_during_your_turn")'


if __name__ == "__main__":
    test_until_end_of_turn()
    test_until_next_end_step()
    test_multi_sentence_impulse()
    test_existing_frequency_unchanged()
    print("ok")
