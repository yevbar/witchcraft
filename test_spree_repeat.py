"""test_spree_repeat.py — 2020s coverage: Spree mode lines (OTJ) + the trailing-repetition modifier.

Both are last-resort, faithful-or-abstain parser handlers added to lift 2020s-mechanic card coverage:

  * Spree '+ {cost} — <effect>' (§702.172): one mode of a Spree spell. Emitted as a `mode_option` (the
    relation sim.py folds into the card's offered `modes`), the body parsed as a normal effect, the per-mode
    additional cost preserved as a descriptive `static`. The '+ {cost} —' prefix is unique to Spree, so it
    can never fire on a non-Spree line.
  * Trailing repetition '<effect> twice / N times' (incubate, manifest dread, investigate, ...): the §701
    'do it again' modifier, folded into the effect's cond as `repeat_<n>`. Added as the LAST branch of
    parse_clause, so it only ever converts an otherwise-ungrounded clause — never perturbs a covered parse.

Run: python3 test_spree_repeat.py
"""
from __future__ import annotations

import card_corpus
import ground
from card_effects import parse_clause
from transpile_card import transpile_unit, _SPREE_MODE

CHECKS: list = []


def check(name: str, cond: bool) -> None:
    CHECKS.append((name, bool(cond)))


def _unit(raw: str):
    return card_corpus.Unit(card="X", raw=raw, template=raw)


def _facts(raw: str, seq: int = 0):
    o = transpile_unit(_unit(raw), {"id": "x", "card": {"name": "X"}, "seq": seq})
    return o.facts if o else None


def _spree() -> None:
    f = _facts("+ {1} — Destroy target artifact.", seq=1)
    check("spree mode line grounds", f is not None)
    check("spree mode emits a mode_option", any(s.startswith('mode_option("x", "spree1")') for s in (f or [])))
    check("spree mode grounds the body effect (destroy target_artifact)",
          any('"destroy"' in s and "target_artifact" in s for s in (f or [])))
    check("spree mode preserves the additional cost as a static",
          any('spree_spree1_cost_1' in s for s in (f or [])))
    # a multi-mana cost + a multi-sentence body still grounds
    f2 = _facts("+ {3}{W}{W} — Destroy all creatures.", seq=2)
    check("spree mode with a colored cost grounds", f2 is not None and any('cost_3_w_w' in s for s in f2))

    # a real Spree card fully ingests (every line parses)
    cards = {c["name"]: c for c in card_corpus.load_cards()}
    rr = cards.get("Requisition Raid")
    if rr:
        cid = ground.slug("Requisition Raid")
        full = all(transpile_unit(u, {"id": cid, "card": rr, "seq": i})
                   for i, u in enumerate(card_corpus.units_of(rr)))
        check("Requisition Raid (a Spree card) fully ingests", full)

    # the '+ {cost} —' prefix never matches a non-Spree line shape
    check("spree regex ignores loyalty '[+1]:'", not _SPREE_MODE.match("[+1]: Draw a card."))
    check("spree regex ignores a mid-text '+1/+1'", not _SPREE_MODE.match("Target creature gets +1/+1."))


def _repeat() -> None:
    for raw, n in [("Manifest dread twice", "2"), ("Incubate 2 twice", "2"),
                   ("Investigate twice", "2"), ("Manifest dread three times", "3")]:
        e = parse_clause(raw)
        check(f"'{raw}' grounds with cond repeat_{n}", e is not None and e.cond == f"repeat_{n}")
    # 'X times' keeps the variable
    e = parse_clause("Incubate 1 X times")
    check("'Incubate 1 X times' grounds with cond repeat_x", e is not None and e.cond == "repeat_x")
    # LAST-RESORT: a clause that already grounds is untouched (no spurious repeat)
    base = parse_clause("Surveil 2")
    check("'Surveil 2' is unchanged (repeat handler is last-resort)",
          base is not None and base.cond == "-")
    base2 = parse_clause("Draw a card")
    check("'Draw a card' is unchanged", base2 is not None and base2.cond == "-")
    # the count word is restricted: a non-numeric '... times' tail does NOT trigger a repeat
    notrep = parse_clause("Manifest dread at all times")
    check("a non-numeric '... times' tail does not falsely repeat",
          notrep is None or not str(notrep.cond).startswith("repeat_"))


def run() -> None:
    _spree()
    _repeat()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
