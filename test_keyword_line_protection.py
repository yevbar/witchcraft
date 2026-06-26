"""§702 keyword line with a trailing 'protection from <X>': split it out as a separate keyword.

A line like 'First strike, protection from black and from red' or 'Flying, protection from red' used to mis-
parse — _ground_kw doesn't ground 'protection from <X>', so _kw_line abstained and _kw_param swallowed the
whole protection tail into the FIRST keyword's param (keyword_param(first_strike, 'protection_from_…')).
_kw_line now grounds a 'protection from <X>' comma-part too, so the keywords split cleanly and protection
gets its own printed_keyword + keyword_param(protection, from_<X>) — which also feeds the engine's
protection_from targeting wire.
"""
import os
os.environ.setdefault("MTG_NO_SPACY", "1")

import card_corpus
import ground
from transpile_card import transpile_unit


def _facts(text, name="T"):
    c = {"name": name, "text": text}
    cid = ground.slug(name)
    o = transpile_unit(card_corpus.units_of(c)[0], {"id": cid, "card": c, "seq": 0})
    return o.facts if o else None


def _kw(facts):
    return {f.split('"')[3] for f in facts if f.startswith("printed_keyword")}


def test_keyword_then_protection_splits():
    f = _facts("Flying, protection from red")
    assert f is not None
    assert _kw(f) == {"flying", "protection"}
    assert any('keyword_param("t", "protection", "from_red")' in x for x in f)
    # the bug was: keyword_param(flying, "protection_from_red") — must NOT happen
    assert not any('"flying", "protection' in x for x in f)


def test_compound_colour_protection():
    f = _facts("First strike, protection from black and from red")
    assert _kw(f) == {"first_strike", "protection"}
    assert any('keyword_param("t", "protection", "from_black_and_from_red")' in x for x in f)


def test_long_keyword_list_with_protection():
    f = _facts("Flying, first strike, vigilance, trample, haste, protection from black and from red")
    assert _kw(f) == {"flying", "first_strike", "vigilance", "trample", "haste", "protection"}


def test_non_colour_protection_quality():
    f = _facts("Vigilance, protection from creatures")
    assert _kw(f) == {"vigilance", "protection"}
    assert any('keyword_param("t", "protection", "from_creatures")' in x for x in f)


def test_plain_keyword_list_unchanged():
    f = _facts("Flying, vigilance")
    assert _kw(f) == {"flying", "vigilance"}
    assert not any("keyword_param" in x for x in f)


def test_unsplittable_multicolour_comma_still_single_protection():
    # 'Protection from red, white, and blue' — commas INSIDE the colour list, no leading keyword: stays one
    # protection keyword (can't be split on those commas).
    f = _facts("Protection from red, white, and blue")
    assert _kw(f) == {"protection"}


if __name__ == "__main__":
    test_keyword_then_protection_splits()
    test_compound_colour_protection()
    test_long_keyword_list_with_protection()
    test_non_colour_protection_quality()
    test_plain_keyword_list_unchanged()
    test_unsplittable_multicolour_comma_still_single_protection()
    print("ok")
