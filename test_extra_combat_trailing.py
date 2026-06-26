"""§505 'there is an additional combat phase after this phase' — the TRAILING 'after this phase' qualifier.

The _extra_combat / ECOMBAT family already grounded 'there is an additional combat phase' and the older
LEADING 'after this phase, there is …' form, but the DOMINANT corpus shape puts the qualifier TRAILING
('… additional combat phase after this phase [followed by an additional main phase]'), which left the clause
un-reducible (ECOMBAT matched only the prefix, stranding 'after this phase'). Extending the whole-phrase
terminal to consume the trailing qualifier grounds them all to the same constant extra_combat(-, you) tuple.
"""
import os
os.environ.setdefault("MTG_NO_SPACY", "1")

from card_effects import parse_clause


def test_trailing_after_this_phase():
    e = parse_clause("there is an additional combat phase after this phase")
    assert e is not None and e.verb == "extra_combat" and e.target == "you"


def test_trailing_after_this_main_phase():
    e = parse_clause("there is an additional combat phase after this main phase")
    assert e is not None and e.verb == "extra_combat"


def test_trailing_followed_by_main():
    e = parse_clause("there is an additional combat phase after this phase followed by an additional main phase")
    assert e is not None and e.verb == "extra_combat"


def test_conditional_wrapped():
    e = parse_clause("If it's your main phase, there is an additional combat phase after this phase")
    assert e is not None and e.verb == "extra_combat"
    assert e.cond and e.cond != "-"


def test_existing_forms_unchanged():
    for t in ("there is an additional combat phase",
              "after this phase, there is an additional combat phase"):
        e = parse_clause(t)
        assert e is not None and e.verb == "extra_combat"


if __name__ == "__main__":
    test_trailing_after_this_phase()
    test_trailing_after_this_main_phase()
    test_trailing_followed_by_main()
    test_conditional_wrapped()
    test_existing_forms_unchanged()
    print("ok")
