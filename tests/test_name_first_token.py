"""Name-first token creation: 'create <Name>, a/an [legendary] <P/T> … token' grounds.

The named legendary-token form ('create Marit Lage, a legendary 20/20 black Avatar creature token')
puts the token's flavor name BEFORE the type line, which the create-token leaf can't parse. parse_clause
STRUCTURALLY reorders it (drops the leading name — loss-free, since the leaf already drops a token's flavor
name) so the canonical 'create a/an <P/T> … token' tail grounds. Tightly gated: capitalized name + article
+ a P/T spec, so a compound 'create a 1/1 token, a 2/2 token' (article-led) can't be mis-rewritten.
"""

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import os
os.environ.setdefault("MTG_NO_SPACY", "1")

from interpreter.card_effects import parse_clause


def _t(e):
    return (e.verb, str(e.amount), e.target, e.extra, e.cond) if e else None


def test_name_first_grounds():
    cases = {
        "Create Phobos, a legendary 3/2 red Horse creature token":
            ("create", "1", "token", "legendary_3_2_red_horse_creature", "-"),
        "create Marit Lage, a legendary 20/20 black Avatar creature token with flying":
            ("create", "1", "token", "legendary_20_20_black_avatar_creature", "-"),
        "Create Vecna, a legendary 8/8 black Zombie God creature token with indestructible":
            ("create", "1", "token", "legendary_8_8_black_zombie_god_creature", "-"),
        # multi-word name with a lowercase particle
        "create Tuktuk the Returned, a legendary 5/5 colorless Goblin Golem artifact creature token":
            ("create", "1", "token", "legendary_5_5_colorless_goblin_golem_artifact_creature", "-"),
    }
    for s, want in cases.items():
        assert _t(parse_clause(s)) == want, s


def test_matches_canonical_named_order():
    # both orders ('<Name>, a …' and 'a … named <Name>') must yield the same grounded type spec
    a = parse_clause("Create Phobos, a legendary 3/2 red Horse creature token")
    b = parse_clause("create a legendary 3/2 red Horse creature token named Phobos")
    assert a and b and a.target == b.target == "token" and a.extra == b.extra


def test_guards_no_corruption():
    # a non-token 'create <Name>, a …' has no P/T -> the rewrite must not fire (stays ungrounded here)
    assert parse_clause("Create Phobos") is None
    # canonical forms unaffected
    assert _t(parse_clause("Create a Treasure token")) == ("create", "1", "token", "treasure", "-")
    assert _t(parse_clause("Create a legendary 8/8 white Angel creature token")) == \
        ("create", "1", "token", "legendary_8_8_white_angel_creature", "-")


if __name__ == "__main__":
    test_name_first_grounds()
    test_matches_canonical_named_order()
    test_guards_no_corruption()
    print("ok")
