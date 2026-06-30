"""Quoted-ability grant with a trailing non-EOT duration: '<who> gains "<ability>" until <X>'.

The §613.6 quoted-ability grant grounded bare and with 'until end of turn', but a trailing non-standard
duration ('until ~ is cast from exile', 'until your next turn') made _QUOTED_GRANT's gate miss the clause,
so later surface rewrites shattered the inner quote (the comma-bearing mana abilities especially) -> None.
The gate now accepts any trailing 'until <duration>', peeled structurally into the grant's cond.
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)
import os
os.environ.setdefault("MTG_NO_SPACY", "1")

from interpreter.card_effects import parse_clause


def _t(e):
    return (e.verb, str(e.amount), e.target, e.extra, e.cond) if e else None


def test_trailing_until_duration_captured():
    # the New Capenna 'Exile from hand: land gains mana ability' cycle (comma-bearing quoted ability)
    assert _t(parse_clause('target land gains "{T}: Add {U}, {B}, or {R}" until ~ is cast from exile')) == \
        ("grant_ability", "-", "target_land", "t_add_u_b_or_r", "until_is_cast_from_exile")
    assert _t(parse_clause('target creature gains "{T}: Add {C}" until your next turn')) == \
        ("grant_ability", "-", "target_creature", "t_add_c", "until_your_next_turn")


def test_existing_forms_unchanged():
    assert _t(parse_clause('target creature gains "{T}: Add {C}"')) == \
        ("grant_ability", "-", "target_creature", "t_add_c", "-")
    assert _t(parse_clause('target creature gains "flying" until end of turn')) == \
        ("grant_ability", "until_end_of_turn", "target_creature", "flying", "-")


if __name__ == "__main__":
    test_trailing_until_duration_captured()
    test_existing_forms_unchanged()
    print("ok")
