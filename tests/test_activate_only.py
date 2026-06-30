"""test_activate_only.py — §602.5 activated-ability timing/frequency restriction ('Activate … only …').

The single largest recurring failing LEAF clause among blocked cards ('Activate only as a sorcery' ~86×,
plus 'only once each turn' / 'only during your turn' / 'only if <cond>'). It's the trailing restriction
sentence of an activated ability, so the whole ability abstained (all-or-nothing) when the leaf couldn't
ground it. A native Lark production (ACT_ONLY anchor, NO grammar collision with the bare 'activate' keyword
action — that lacks the 'only') grounds it as activate_only(-, -, slug(restriction)); the restriction (a
timing/frequency phrase OR an 'if <cond>') rides the slug. _activated/_static_effect then emit it via the leaf.

Run: MTG_NO_SPACY=1 python3 test_activate_only.py
"""
from __future__ import annotations

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from interpreter import card_corpus
from interpreter import ground
from interpreter.card_effects import parse_clause
from interpreter.transpile_card import transpile_unit

CHECKS: list = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _clauses() -> None:
    for src, extra in [
        ("activate only as a sorcery", "as_a_sorcery"),
        ("activate this ability only as a sorcery", "as_a_sorcery"),
        ("activate only once each turn", "once_each_turn"),
        ("activate this ability only during your turn", "during_your_turn"),
        ("activate only during combat", "during_combat"),
        ("activate only if you control three or more creatures with different powers",
         "if_you_control_three_or_more_creatures_with_different_powers"),
    ]:
        e = parse_clause(src)
        check(f"{src[:40]!r} -> activate_only({extra[:24]})",
              e is not None and e.verb == "activate_only" and e.target == "-" and e.extra == extra)


def _guards() -> None:
    # the bare 'activate' keyword action is untouched (no 'only' -> ACT_ONLY can't match)
    e = parse_clause("activate target artifact")
    check("keyword-action 'activate target artifact' unchanged",
          e is not None and e.verb == "activate" and e.target == "target_artifact")
    # a non-activate clause with 'only' elsewhere is unaffected
    e = parse_clause("destroy target creature")
    check("'destroy target creature' unchanged", e is not None and e.verb == "destroy")


def _cards() -> None:
    cards = {c["name"]: c for c in card_corpus.load_cards()}
    for n in ["Sungold Sentinel", "Forgestoker Dragon", "Wishing Well"]:
        c = cards.get(n)
        if not c:
            continue
        cid = ground.slug(n)
        outs = [transpile_unit(u, {"id": cid, "card": c, "seq": i})
                for i, u in enumerate(card_corpus.units_of(c))]
        check(f"{n} fully ingests (activate-only restriction grounds)", all(outs))
        facts = [f for o in outs if o for f in o.facts]
        check(f"{n} records an activate_only fact", any("activate_only" in f for f in facts))


def run() -> None:
    _clauses()
    _guards()
    _cards()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
