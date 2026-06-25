"""test_migrate_rad_counters.py — '<player> gets <N> rad counter(s)' (§122 radiation) now grounds in lark.

This is a FAITHFUL IMPROVEMENT, not a byte-identical flip: the regex grounds 'target player gets two rad
counters' LOSSILY via the generic 'counter <obj>' leaf as ('counter', target='target_player_gets_two_rad') —
garbage. lark's pgcclause grounds player-resource counters NATIVELY but gated its kind allow-list to
poison/energy/experience, so 'rad' abstained and fell to that garbage. Adding 'rad' to the allow-list (a real
player-resource counter, ~14 corpus clauses / 23 cards) makes lark ground them as put_counter(<N|X>, player, rad)
— strictly better. Purely additive: the existing poison/energy/experience groundings are unchanged (DIFFERS=0
vs regex), and the allow-list can only make previously-abstaining 'rad' clauses ground.

Run: MTG_NO_SPACY=1 python3 test_migrate_rad_counters.py
"""
from __future__ import annotations

from card_effects import parse_effect, parse_clause
from card_lark import parse_clause_lark

CHECKS: list = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _tup(e):
    return (e.verb, str(e.amount), e.target, e.extra, e.cond) if e else None


def run() -> None:
    # rad now grounds faithfully as put_counter on the player (the kind in the EXTRA slot, like poison)
    rad = [
        ("target player gets two rad counters", ("put_counter", "2", "target_player", "rad", "-")),
        ("each player gets a rad counter", ("put_counter", "1", "each_player", "rad", "-")),
        ("each player gets x rad counters", ("put_counter", "X", "each_player", "rad", "-")),
        ("target player gets four rad counters", ("put_counter", "4", "target_player", "rad", "-")),
        ("each player gets two rad counters", ("put_counter", "2", "each_player", "rad", "-")),
    ]
    for s, want in rad:
        check(f"lark grounds {s!r} -> {want}", _tup(parse_clause_lark(s)) == want)
        # parse_clause is lark-first, so the faithful grounding wins over the regex garbage
        check(f"parse_clause (lark-first) grounds {s[:34]!r} faithfully", _tup(parse_clause(s)) == want)
        # and it IS an improvement: the regex alone produced a lossy 'counter' garbage tuple
        rg = parse_effect(s)
        check(f"regex alone was lossy garbage for {s[:30]!r} (improvement confirmed)",
              rg is not None and rg.verb == "counter" and "rad" in rg.target and _tup(rg) != want)

    # GUARD: the existing player-counter kinds are UNCHANGED (byte-identical to regex)
    for s, want in [("you get a poison counter", ("put_counter", "1", "you", "poison", "-")),
                    ("target player gets a poison counter", ("put_counter", "1", "target_player", "poison", "-")),
                    ("you get an experience counter", ("put_counter", "1", "you", "experience", "-"))]:
        lk = parse_clause_lark(s)
        check(f"unchanged: {s!r} -> {want}", _tup(lk) == want and _tup(lk) == _tup(parse_effect(s)))

    # GUARD: a non-player counter kind still abstains here (the allow-list gate still does its job)
    check("'put a +1/+1 counter on target creature' is NOT a player-gets-counter (unchanged)",
          (lambda e: e is not None and e.verb == "put_counter" and e.extra == "+1/+1")(
              parse_clause_lark("put a +1/+1 counter on target creature")))
    check("'target player gets two foo counters' (bogus kind) abstains",
          parse_clause_lark("target player gets two foo counters") is None)

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
