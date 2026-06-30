"""Two small leaf/structure gaps from recent (2024-2025) modal cards:

1. manifest-N: 'manifest the top N cards of your library' (plural) now grounds like the singular form.
2. punctuated modal mode-names: '• Wake Up! — Return …' / '• Combine Powers! — Put …' — the flavor
   mode-name strip in _mode_option now allows punctuation, so the (groundable) body is reached.
"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)
import os
os.environ.setdefault("MTG_NO_SPACY", "1")

from card_effects import parse_clause
from transpile_card import _mode_option


def _t(e):
    return (e.verb, str(e.amount), e.target, e.extra, e.cond) if e else None


class _U:
    def __init__(self, raw):
        self.raw = raw


def test_manifest_count():
    assert _t(parse_clause("manifest the top card of your library")) == \
        ("manifest", "1", "top_of_library", "-", "-")          # singular unchanged (byte-identical)
    assert _t(parse_clause("manifest the top two cards of your library")) == \
        ("manifest", "2", "top_of_library", "-", "-")
    assert _t(parse_clause("manifest the top three cards of your library")) == \
        ("manifest", "3", "top_of_library", "-", "-")
    assert _t(parse_clause("manifest the top 3 cards of your library")) == \
        ("manifest", "3", "top_of_library", "-", "-")


def test_manifest_other_phrasings_still_abstain():
    # not the fixed 'your library' shape -> abstain (kept for existing/other handling)
    assert parse_clause("manifest the top card of target player's library") is None


def _mode_facts(raw):
    o = _mode_option(_U(raw), {"id": "t", "seq": 1})
    return o.facts if o else None


def test_punctuated_mode_name_strips():
    f = _mode_facts("• Wake Up! — Return target creature card from a graveyard to its owner's hand.")
    assert f and any('"return_to_hand"' in x for x in f)
    f = _mode_facts("• Combine Powers! — Put three +1/+1 counters on target creature.")
    assert f and any('"put_counter"' in x for x in f)


if __name__ == "__main__":
    test_manifest_count()
    test_manifest_other_phrasings_still_abstain()
    test_punctuated_mode_name_strips()
    print("ok")
