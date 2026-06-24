"""test_cant_restriction.py — active-voice player restriction statics (slice 2 of can't/restriction).

The non-combat, non-cast '<player-set> can't <verb> <obj>' restrictions the combat frames (_ns_cant) lack:
play lands/cards (§116/§305), draw (§120), search libraries (§701.18), and the §104 game-end pair
(lose/win the game). `_ns_player_restrict` (card_lark, symmetric with `_ns_cast`/`_ns_cant`, same nscant
transformer) grounds each as a distinct cant_<verb> with the object (incl. a 'more than N … each turn'
LIMIT) preserved in the slug. Faithful-or-abstain: a player-set subject, no compound action / conditional
rider (those abstain — a compound splits into two facts via the clause splitter, an 'if' peels into cond).

Run: MTG_NO_SPACY=1 python3 test_cant_restriction.py
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
    for src, verb, who, extra in [
        ("players can't play lands", "cant_play", "players", "lands"),
        ("you can't play lands", "cant_play", "you", "lands"),
        ("players can't draw cards", "cant_draw", "players", "cards"),
        ("each opponent can't draw more than one card each turn", "cant_draw", "each_opponent", "more_than_one_card_each_turn"),
        ("players can't search libraries", "cant_search", "players", "libraries"),
        ("players can't search libraries this turn", "cant_search", "players", "libraries_this_turn"),
    ]:
        e = parse_clause(src)
        check(f"{src[:42]!r} -> {verb}({who}, {extra})",
              e is not None and e.verb == verb and e.target == who and e.extra == extra)

    # §104 game-end: the verb states it fully (no object slug)
    for src, verb, who in [("you can't lose the game", "cant_lose_game", "you"),
                           ("your opponents can't win the game", "cant_win_game", "your_opponents")]:
        e = parse_clause(src)
        check(f"{src!r} -> {verb}({who})", e is not None and e.verb == verb and e.target == who)


def _abstains_and_splits() -> None:
    # a compound NON-cast action is a conflation -> the WHOLE-clause leaf abstains
    for src in ["you can't play lands or cast spells from your hand",
                "players can't draw cards or gain life",
                "players can't pay life or sacrifice creatures to cast spells or activate abilities"]:
        e = parse_clause(src)
        check(f"conflation abstains: {src[:40]!r}", e is None or not e.verb.startswith("cant_"))

    # the combat path is untouched
    e = parse_clause("~ can't attack")
    check("combat '~ can't attack' unchanged", e is not None and e.verb == "cant_attack")
    # a non-player subject doesn't play/draw/search
    e = parse_clause("enchanted creature can't draw cards")
    check("non-player subject abstains", e is None or not e.verb.startswith("cant_"))


def _cards_full() -> None:
    cards = {c["name"]: c for c in card_corpus.load_cards()}
    # clean single-restriction cards that previously abstained
    for n in ["Worms of the Earth", "Aggressive Mining", "Maralen of the Mornsong", "Omen Machine",
              "Narset, Parter of Veils", "Leovold, Emissary of Trest", "Mindlock Orb", "Stranglehold",
              "Lich's Mastery", "Liliana's Undead Minion", "Shadow of Doubt"]:
        c = cards.get(n)
        if not c:
            continue
        cid = ground.slug(n)
        # the restriction LINE itself must ground (the card may have other unrelated blocked lines)
        outs = [(u.raw, transpile_unit(u, {"id": cid, "card": c, "seq": i}))
                for i, u in enumerate(card_corpus.units_of(c))]
        restr = [o for raw, o in outs if "can't play" in raw.lower() or "can't draw" in raw.lower()
                 or "can't search" in raw.lower() or "can't lose the game" in raw.lower()]
        check(f"{n}: its restriction line grounds", restr and all(restr))

    # the different-subject compound SPLITS into two faithful facts (not one lossy one)
    c = cards.get("Abyssal Persecutor")
    if c:
        outs = [transpile_unit(u, {"id": "abyssal_persecutor", "card": c, "seq": i})
                for i, u in enumerate(card_corpus.units_of(c))]
        facts = [f for o in outs if o for f in o.facts]
        check("Abyssal Persecutor: compound splits to cant_win_game(you) + cant_lose_game(opponents)",
              any("cant_win_game" in f and '"you"' in f for f in facts)
              and any("cant_lose_game" in f and "opponents" in f for f in facts))


def run() -> None:
    _clauses()
    _abstains_and_splits()
    _cards_full()
    passed = sum(1 for _, ok in CHECKS if ok)
    for name, ok in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"\n{passed}/{len(CHECKS)} checks passed")
    if passed != len(CHECKS):
        raise SystemExit(1)


if __name__ == "__main__":
    run()
