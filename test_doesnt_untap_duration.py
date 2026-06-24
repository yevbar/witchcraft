"""test_doesnt_untap_duration.py — §502 no-untap static with a 'for as long as <X>' duration.

The lock-down archetype ('Tap target creature. It doesn't untap during its controller's untap step for as
long as ~ remains tapped/on the battlefield' — Dungeon Geists, Icefall Regent, Tidebinder Mage, the Courier
cycle, Shipbreaker Kraken, …) was BLOCKED: the FORASLONGAS terminal (the impulse play-duration anchor)
out-prioritized the nsuntap rule, so play_dur STOLE the clause and abstained. Fix: FORASLONGAS is now also a
legal nstail token (so nsclause can consume the tail) and play_dur is negative-priority (its own play/cast
guard still rejects non-play/cast), so nsuntap wins. The duration is CAPTURED in cond ('~'->self), not dropped.

Run: MTG_NO_SPACY=1 python3 test_doesnt_untap_duration.py
"""
from __future__ import annotations

import card_corpus
import ground
from card_effects import parse_clause
from transpile_card import transpile_unit

CHECKS: list = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _clauses() -> None:
    for src, who, cond in [
        ("it doesn't untap during its controller's untap step for as long as ~ remains tapped",
         "it", "for_as_long_as_self_remains_tapped"),
        ("that creature doesn't untap during its controller's untap step for as long as you control ~",
         "that_creature", "for_as_long_as_you_control_self"),
    ]:
        e = parse_clause(src)
        check(f"{src[:44]!r} -> doesnt_untap({who}, {cond[:24]})",
              e is not None and e.verb == "doesnt_untap" and e.target == who and e.cond == cond)
    # the plain no-duration form is unchanged (cond '-')
    e = parse_clause("~ doesn't untap during its controller's untap step")
    check("plain 'doesn't untap during … untap step' unchanged (cond '-')",
          e is not None and e.verb == "doesnt_untap" and e.cond == "-")
    # the 'next untap step' operand still captured in extra
    e = parse_clause("~ doesn't untap during its controller's next untap step")
    check("'next untap step' still captured (extra 'next')",
          e is not None and e.verb == "doesnt_untap" and e.extra == "next")


def _no_regress_play_duration() -> None:
    # the impulse play-duration family (play_dur) must still ground at its new lower priority
    e = parse_clause("play it for as long as you control this creature")
    check("play-duration 'play it for as long as …' still grounds",
          e is not None and e.verb == "play" and "for_as_long_as" in e.cond)


def _cards() -> None:
    cards = {c["name"]: c for c in card_corpus.load_cards()}
    for n in ["Dungeon Geists", "Icefall Regent", "Mole Worms", "Tidebinder Mage", "Shipbreaker Kraken"]:
        c = cards.get(n)
        if not c:
            continue
        cid = ground.slug(n)
        outs = [transpile_unit(u, {"id": cid, "card": c, "seq": i})
                for i, u in enumerate(card_corpus.units_of(c))]
        check(f"{n} fully ingests (no-untap duration grounds)", all(outs))


def run() -> None:
    _clauses()
    _no_regress_play_duration()
    _cards()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
