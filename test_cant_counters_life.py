"""test_cant_counters_life.py — restriction statics slice 4: gain-life, counters, untap-limits.

Three deferred restriction groups, each handled faithfully:
  * gain-life — '<player-set> can't gain life [<duration>]'. NB this clause routes to the keyword-GRANT
    production (the dynamic lexer WORD-tokenizes "can't" when nscant fails, and gain∈GVERB), so it's grounded
    in `_ToEffect.grant` (not the nscant chain) -> cant_gain_life(-, subj, cond=<duration>).
  * counters — active '<player> can't get [<kind>] counters' -> cant_get_counters; passive 'counters can't be
    put on <recipients>' -> cant_put_counters; '<X> can't have [more than N] <kind> counters' -> cant_have_counters.
  * untap-limit — '<player> can't untap more than N <permanents> during <their> untap step[s]' -> cant_untap.
Every qualifier/LIMIT rides the slug; a compound ('gain life OR remove poison counters') abstains.

Run: MTG_NO_SPACY=1 python3 test_cant_counters_life.py
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
    for src, verb, who, cond in [
        ("enchanted player can't gain life", "cant_gain_life", "enchanted_player", "-"),
        ("your opponents can't gain life this turn", "cant_gain_life", "your_opponents", "this_turn"),
        ("that player can't gain life for the rest of the game", "cant_gain_life", "that_player", "for_the_rest_of_the_game"),
    ]:
        e = parse_clause(src)
        check(f"{src[:40]!r} -> {verb}({who}, {cond})",
              e is not None and e.verb == verb and e.target == who and e.cond == cond)

    for src, verb, who, extra in [
        ("players can't get counters", "cant_get_counters", "players", "counters"),
        ("you can't get poison counters", "cant_get_counters", "you", "poison_counters"),
        ("counters can't be put on artifacts, creatures, enchantments, or lands", "cant_put_counters", "artifacts_creatures_enchantments_or_lands", "-"),
        ("~ can't have more than seven dream counters on it", "cant_have_counters", "self", "more_than_seven_dream_counters_on_it"),
        ("players can't untap more than two permanents during their untap steps", "cant_untap", "players", "more_than_two_permanents_during_their_untap_steps"),
        ("you can't untap more than one land during your untap step", "cant_untap", "you", "more_than_one_land_during_your_untap_step"),
    ]:
        e = parse_clause(src)
        check(f"{src[:44]!r} -> {verb}({who}, {extra})",
              e is not None and e.verb == verb and e.target == who and e.extra == extra)


def _guards() -> None:
    # a compound (gain life OR another action) is a conflation -> abstain
    e = parse_clause("your opponent can't gain life or remove poison counters")
    check("conflation 'gain life or remove poison counters' abstains",
          e is None or e.verb != "cant_gain_life")
    # a REAL gain-life grant is untouched (no 'can't' -> the grant production grounds gain_life)
    e = parse_clause("you gain 3 life")
    check("real 'you gain 3 life' still grounds as gain_life", e is not None and e.verb == "gain_life")
    # counter frame only fires when the clause is about counters (no generic 'have' over-claim)
    e = parse_clause("enchanted creature can't have flying")
    check("'can't have flying' not claimed as cant_have_counters",
          e is None or e.verb != "cant_have_counters")


def _cards() -> None:
    cards = {c["name"]: c for c in card_corpus.load_cards()}
    for n in ["Solemnity", "Static Orb", "Mungha Wurm", "Grievous Wound", "Melira, Sylvok Outcast",
              "Stigma Lasher", "Roiling Vortex", "Skullcrack"]:
        c = cards.get(n)
        if not c:
            continue
        cid = ground.slug(n)
        outs = [transpile_unit(u, {"id": cid, "card": c, "seq": i})
                for i, u in enumerate(card_corpus.units_of(c))]
        check(f"{n} fully ingests", all(outs))

    # Solemnity's TWO restriction lines ground as the two distinct counter facts
    c = cards.get("Solemnity")
    if c:
        facts = [f for i, u in enumerate(card_corpus.units_of(c))
                 for f in (transpile_unit(u, {"id": "solemnity", "card": c, "seq": i}) or type("X", (), {"facts": []})).facts]
        check("Solemnity: cant_get_counters(players) emitted", any("cant_get_counters" in f and "players" in f for f in facts))
        check("Solemnity: cant_put_counters(<types>) emitted", any("cant_put_counters" in f for f in facts))

    # Skullcrack's multi-sentence body splits into the gain-life + prevention restrictions + the burn
    c = cards.get("Skullcrack")
    if c:
        facts = [f for i, u in enumerate(card_corpus.units_of(c))
                 for f in (transpile_unit(u, {"id": "skullcrack", "card": c, "seq": i}) or type("X", (), {"facts": []})).facts]
        check("Skullcrack: cant_gain_life + cant_prevent_damage both emitted",
              any("cant_gain_life" in f for f in facts) and any("cant_prevent_damage" in f for f in facts))


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
