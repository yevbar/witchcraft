"""test_freq_restriction.py — §603.3 frequency restriction tails ('This ability triggers only …' / 'Do this
only …'), the sibling of test_activate_only. Same restriction-tail shape, reusing the acronlyrest span:
TRG_ONLY -> triggers_only(-, -, slug(restriction)); DOTHIS_ONLY -> do_this_only(-, -, slug(restriction)).
Distinctive contiguous anchors, so no theft of a bare 'triggers'/'do this'.

Run: MTG_NO_SPACY=1 python3 test_freq_restriction.py
"""
from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from interpreter.card_effects import parse_clause

CHECKS: list = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def run() -> None:
    for src, verb, extra in [
        ("this ability triggers only once each turn", "triggers_only", "once_each_turn"),
        ("this ability triggers only once each game", "triggers_only", "once_each_game"),
        ("do this only once each turn", "do_this_only", "once_each_turn"),
        ("do this only during your turn", "do_this_only", "during_your_turn"),
    ]:
        e = parse_clause(src)
        check(f"{src[:38]!r} -> {verb}({extra})",
              e is not None and e.verb == verb and e.target == "-" and e.extra == extra)
    # guards: the distinctive anchors don't fire on a bare 'triggers'/'do this'
    check("'this ability triggers when ~ attacks' not claimed",
          parse_clause("this ability triggers when ~ attacks") is None
          or parse_clause("this ability triggers when ~ attacks").verb != "triggers_only")
    check("'if you do this, draw a card' not claimed as do_this_only",
          (lambda e: e is None or e.verb != "do_this_only")(parse_clause("if you do this, draw a card")))
    # the activate_only sibling is unaffected
    e = parse_clause("activate only as a sorcery")
    check("activate_only sibling still grounds", e is not None and e.verb == "activate_only")

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
