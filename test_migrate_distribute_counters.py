"""test_migrate_distribute_counters.py — 'distribute N <kind> counters among <targets>' (§122) migrated off
the dedicated `_distribute_counters` regex template onto the lark AST; the template is DELETED.

A leading DISTRIBUTE terminal anchors the clause (dcclause -> distribute_v); dcbody spans the rest (commas
live inside the WORD terminal, so 'one, two, or three target creatures' parses). The transformer re-applies the
template's EXACT regex to self._src -> put_counter(<n|X>, _target(targets), <kind>, distributed). A P/T kind
('+1/+1') is kept raw; a word kind is slugged. The production is NEGATIVE priority, so any competing parse wins
(one earley tree, no fallthrough). All corpus distribute clauses ground in lark byte-identically (0 abstains),
and lark additionally recovers the bulleted '• Distribute …' variant that the `^distribute`-anchored regex missed.
A parse_clause snapshot over every distribute-bearing clause was IDENTICAL before/after deleting the template, so
the tuple is asserted DIRECTLY here (parse_effect now returns None).

Run: MTG_NO_SPACY=1 python3 test_migrate_distribute_counters.py
"""
from __future__ import annotations

from card_lark import parse_clause_lark
from card_effects import parse_clause

CHECKS: list = []


def check(name, cond):
    CHECKS.append((name, bool(cond)))


def _tup(e):
    return (e.verb, str(e.amount), e.target, e.extra, e.cond) if e else None


def run() -> None:
    # the template is DELETED -> assert the migrated tuple directly (byte-identity proven by the snapshot)
    cases = [
        ("distribute three +1/+1 counters among one, two, or three target creatures",
         ("put_counter", "3", "one_two_or_three_target_creatures", "+1/+1", "distributed")),
        ("distribute two +1/+1 counters among one or two target creatures you control",
         ("put_counter", "2", "one_or_two_target_creatures_you_control", "+1/+1", "distributed")),
        ("distribute four +1/+1 counters among any number of target creatures",
         ("put_counter", "4", "any_number_of_target_creatures", "+1/+1", "distributed")),
        ("distribute x +1/+1 counters among any number of target creatures",
         ("put_counter", "X", "any_number_of_target_creatures", "+1/+1", "distributed")),
        ("distribute two -1/-1 counters among one or two target creatures",
         ("put_counter", "2", "one_or_two_target_creatures", "-1/-1", "distributed")),
    ]
    for src, want in cases:
        check(f"lark grounds {src[:48]!r} -> {want[1]} {want[2][:24]}", _tup(parse_clause_lark(src)) == want)

    # the X amount and the 'distributed' cond marker are preserved
    check("'distribute x …' keeps amount X",
          (lambda e: e is not None and str(e.amount) == "X" and e.cond == "distributed")(
              parse_clause_lark("distribute x +1/+1 counters among any number of target creatures")))

    # BULLETED recovery: the regex `^distribute…` missed the leading '•'; lark strips it and grounds
    check("bulleted '• distribute two +1/+1 …' grounds in lark (regex-missed recovery)",
          _tup(parse_clause_lark("• distribute two +1/+1 counters among one or two target creatures"))
          == ("put_counter", "2", "one_or_two_target_creatures", "+1/+1", "distributed"))

    # guards: unrelated clauses unaffected; 'distribute' fragment alone does not ground
    check("'put a +1/+1 counter on target creature' unchanged",
          (lambda e: e is not None and e.verb == "put_counter" and e.cond != "distributed")(
              parse_clause_lark("put a +1/+1 counter on target creature")))
    check("'distribute' fragment alone does not ground", parse_clause_lark("distribute") is None)

    # lark-first parse_clause still grounds end-to-end (template gone, lark owns it)
    check("parse_clause('distribute three +1/+1 counters among one, two, or three target creatures') grounds",
          (lambda e: e is not None and e.verb == "put_counter" and e.cond == "distributed")(
              parse_clause("distribute three +1/+1 counters among one, two, or three target creatures")))

    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
