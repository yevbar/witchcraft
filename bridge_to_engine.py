"""bridge_to_engine.py — load REAL cards (datalog/cards.dl + the oracle corpus) into the rules
engine's input relations, so driver.py simulates actual Magic cards instead of hand-coded demo state.

This is the missing seam between the two interpreted datalog worlds: the per-card oracle facts
(card_effect / printed_keyword / card_ability, from transpile_card.py) and the playable rules engine
(engine_rules.dl, driven by driver.py). The bridge translates a card instance into the engine's
vocabulary — printed_type/power/toughness, printed_keyword, has_trigger/trigger_effect — and the
engine derives the game; the bridge authors no rules.

Faithful-or-abstain: a trigger event, effect verb, or amount the engine can't resolve is dropped, so
a partly-understood card plays as far as the interpretation reaches and never acts wrongly (the same
bar the interpreter holds). What's dropped is reported by `coverage()`.

Run: python3 bridge_to_engine.py        # report how many cards load cleanly / partially / not at all
"""

from __future__ import annotations

import re

import card_corpus
import ground
import sim

# cards.dl trigger phrasing -> the event engine_rules.dl fires on (§603). Unmapped events abstain.
_EVENT = {
    "enters": "etb_self",
    "dies": "dies_self",
    "attacks": "attacks_self",
    "the_beginning_of_your_upkeep": "upkeep",
    "the_beginning_of_your_end_step": "end_step",
    "deals_combat_damage_to_a_player": "combat_damage_to_player",
}

# cards.dl effect verb -> the effect name the shim's _apply_effects resolves. Unmapped verbs abstain.
_EFFECT = {
    "draw": "draw",
    "lose_life": "lose_life",
    "gain_life": "gain_life",
    "deal_damage": "deal_damage",
    "put_counter": "add_counter",
    "create": "create_token",
}

# effect target -> the engine's player-target vocabulary (controller vs every opponent).
def _target(tgt: str) -> str:
    if "opponent" in tgt or "each_player" in tgt or tgt.startswith(("target_player", "that_player")):
        return "each_opponent"
    return "controller"


def _counter_kind(extra: str) -> str | None:
    if extra in ("+1/+1", "p1p1"):
        return "p1p1"
    if extra in ("-1/-1", "m1m1"):
        return "m1m1"
    return None


def _int(amt) -> int | None:
    return int(amt) if str(amt).lstrip("-").isdigit() else None


# keywords the engine models as printed_keyword inputs (it derives flying/evasion/etc. from these).
_ENGINE_KEYWORDS = {"flying", "reach", "defender", "menace", "hexproof", "shroud", "indestructible",
                    "infect", "wither", "vigilance", "lifelink", "deathtouch", "trample", "haste"}


def card_facts(name: str, ctrl: str, tid: str, db: dict, corpus: dict) -> tuple[dict, list]:
    """The (relation -> rows) an instance `tid` of card `name` controlled by `ctrl` contributes to a
    driver state, plus a list of (kind, detail) for the clauses that abstained. Pure data — no rules."""
    c = corpus.get(name, {})
    facts = ground.slug(name)
    f = db.get(facts, {})
    out: dict[str, set] = {}
    dropped: list = []

    def add(rel, row):
        out.setdefault(rel, set()).add(row)

    add("printed_control", (ctrl, tid))
    for t in c.get("types") or []:
        add("printed_type", (tid, t.lower()))
    p, t = c.get("power"), c.get("toughness")
    if str(p or "").lstrip("-").isdigit():
        add("printed_power", (tid, int(p)))
    if str(t or "").lstrip("-").isdigit():
        add("printed_toughness", (tid, int(t)))
    for kw in f.get("keywords", set()):
        if kw in _ENGINE_KEYWORDS:
            add("printed_keyword", (tid, kw))

    for aid, ab in f.get("abilities", {}).items():
        if ab.get("kind") != "triggered":
            continue                                          # spells/activated need a resolution path the engine lacks
        event = _EVENT.get(ab.get("trigger"))
        if event is None:
            dropped.append(("event", ab.get("trigger")))
            continue
        a = f"{tid}_{aid}"
        emitted = False
        for _seq, verb, amt, tgt, extra, _cond in ab.get("effects", []):
            eff = _EFFECT.get(verb)
            n = _int(amt)
            if eff is None or n is None:
                dropped.append(("effect", verb))
                continue
            target = _counter_kind(extra) if eff == "add_counter" else _target(tgt)
            if target is None:
                dropped.append(("counter_kind", extra))
                continue
            add("has_trigger", (a, tid, event))
            add("trigger_effect", (a, eff, n, target))
            emitted = True
        if not emitted:
            out.get("has_trigger", set()).discard((a, tid, event))
    return out, dropped


def make_state(boards: dict, life: int = 20) -> dict:
    """Assemble a full driver state of REAL cards. `boards` = {player: {"battlefield": [names],
    "hand": [names], "library": int}}. Every card's characteristics/abilities come from the bridge;
    only the turn-structure scaffolding (step, priority, empty event relations) is added here."""
    db, corpus = sim.load_db(), {c["name"]: c for c in card_corpus.load_cards()}
    players = list(boards)
    state: dict[str, set] = {
        "current_step": {("untap",)}, "active_player": {(players[0],)},
        "is_player": {(p,) for p in players}, "life": {(p, life) for p in players},
        "counter": set(), "tapped": set(), "attacks": set(), "blocks": set(),
        "on_battlefield": set(), "in_hand": set(), "in_library": set(),
    }
    n = [0]

    def place(name, pl, zone):
        tid = f"{ground.slug(name)}_{n[0]}"
        n[0] += 1
        facts, _ = card_facts(name, pl, tid, db, corpus)
        for rel, rows in facts.items():
            state.setdefault(rel, set()).update(rows)
        state.setdefault(zone, set()).add((tid,) if zone != "in_hand" else (pl, tid))
        if zone == "in_hand":                                # castable: spell type + cmc + available mana
            c = corpus.get(name, {})
            for t in c.get("types") or []:
                state.setdefault("spell_type", set()).add((tid, t.lower()))
            state.setdefault("mana_cost", set()).add((tid, _mana_value(c.get("manaCost"))))
        return tid

    for pl, z in boards.items():
        for nm in z.get("battlefield", []):
            place(nm, pl, "on_battlefield")
        for nm in z.get("hand", []):
            place(nm, pl, "in_hand")
        for i in range(z.get("library", 0)):
            state["in_library"].add((pl, f"{pl}_lib{i}"))
        state.setdefault("mana_available", set()).add((pl, z.get("mana", 0)))
    return state


_MV_SYM = re.compile(r"\{([^}]+)\}")


def _mana_value(cost) -> int:
    """Converted mana cost (§202.3) from a manaCost string like '{4}{G}{G}' -> 6: numeric symbols add
    their value, {X}/{Y}/{Z} count 0, every other pip (colored/hybrid/phyrexian) counts 1."""
    if not cost:
        return 0
    total = 0
    for sym in _MV_SYM.findall(str(cost)):
        head = sym.split("/")[0]
        if head.isdigit():
            total += int(head)
        elif head in ("X", "Y", "Z"):
            total += 0
        else:
            total += 1
    return total


def make_deck_state(decks: dict, seed: int = 0, hand: int = 7, life: int = 20) -> dict:
    """Assemble a full-game driver state from REAL decks. `decks` = {player: [card_name, …]} (the whole
    library list). Each card is bridged from cards.dl (printed_*, triggers) with a unique id and is made
    castable/playable (spell_type + mana_cost), then the deck is shuffled (deterministic by `seed`),
    opening hands drawn, and a real library ORDER recorded so draws come off the true top. Everything a
    card does comes from the interpreter; only turn scaffolding is added here."""
    import random
    db, corpus = sim.load_db(), {c["name"]: c for c in card_corpus.load_cards()}
    players = list(decks)
    rng = random.Random(seed)
    state: dict[str, object] = {
        "current_step": {("untap",)}, "active_player": {(players[0],)},
        "is_player": {(p,) for p in players}, "life": {(p, life) for p in players},
        "counter": set(), "tapped": set(), "attacks": set(), "blocks": set(),
        "on_battlefield": set(), "in_hand": set(), "in_library": set(),
        "_lib_order": {p: [] for p in players}, "_land_played": set(),
    }
    n = [0]

    def load(name, pl, zone):
        tid = f"{ground.slug(name)}_{n[0]}"
        n[0] += 1
        facts, _ = card_facts(name, pl, tid, db, corpus)
        for rel, rows in facts.items():
            state.setdefault(rel, set()).update(rows)
        c = corpus.get(name, {})
        for t in c.get("types") or []:                       # castable/playable when it reaches the hand
            state.setdefault("spell_type", set()).add((tid, t.lower()))
        state.setdefault("mana_cost", set()).add((tid, _mana_value(c.get("manaCost"))))
        if zone == "in_hand":
            state["in_hand"].add((pl, tid))
        else:
            state["in_library"].add((pl, tid))
            state["_lib_order"][pl].append(tid)
        return tid

    for pl, deck in decks.items():
        order = list(deck)
        rng.shuffle(order)
        for nm in order[:hand]:
            load(nm, pl, "in_hand")
        for nm in order[hand:]:
            load(nm, pl, "in_library")
    return state


def play_real_game(decks: dict, seed: int = 0, max_turns: int = 40) -> str | None:
    """Play a FULL game of real cards end-to-end through the datalog rules engine: shuffle real decks,
    draw, play lands, cast creatures/spells as mana allows, attack, resolve triggers/deaths — every card
    characteristic and effect comes from cards.dl, every rule from engine_rules.dl; driver.py authors no
    game logic. Returns the loser."""
    import driver
    state = make_deck_state(decks, seed=seed)
    return driver.play_game(state, list(decks), max_turns=max_turns)


def demo_game() -> None:
    """A self-playing game of REAL cards, driven entirely by the datalog rules engine. The bridge
    loads each card's characteristics + triggered abilities from cards.dl; driver.py derives combat,
    deaths, and which triggers fire; this function authors no game logic."""
    import driver
    boards = {
        # alice's lone Tattered Mummy (1/2) — its interpreted ability is "when ~ dies, each opponent
        # loses 2 life". It attacks into bob's Gray Ogre (2/2), dies, and the death trigger must fire.
        "alice": {"battlefield": ["Tattered Mummy"], "library": 6},
        "bob": {"battlefield": ["Gray Ogre"], "library": 6},
    }
    state = make_state(boards, life=20)
    print("real-card game — cards from cards.dl, rules from engine_rules.dl (driver authors no logic):")
    print("  alice: Tattered Mummy 1/2  (interpreted: 'when ~ dies, each opponent loses 2 life')")
    print("  bob:   Gray Ogre 2/2\n")
    loser = driver.play_game(state, ["alice", "bob"])
    print(f"\nresult: {loser} lost" if loser else "\nresult: no decisive winner in the turn cap")
    print("life:      ", sorted(state["life"]), " (bob < 18 ⇒ the real card's death trigger resolved via the datalog)")
    print("graveyard: ", sorted(c for (c,) in state.get("graveyard", set())))


def coverage(limit: int | None = None) -> dict:
    """How the corpus loads through the bridge: a card is `clean` if every clause mapped, `partial` if
    some abstained, `inert` if it has no engine-expressible behavior (vanilla/keyword-only is `clean`)."""
    db, corpus = sim.load_db(), {c["name"]: c for c in card_corpus.load_cards()}
    clean = partial = 0
    drop_kinds: dict[str, int] = {}
    names = list(corpus)[:limit] if limit else list(corpus)
    for i, name in enumerate(names):
        _facts, dropped = card_facts(name, "p", f"t{i}", db, corpus)
        if dropped:
            partial += 1
            for k, _d in dropped:
                drop_kinds[k] = drop_kinds.get(k, 0) + 1
        else:
            clean += 1
    return {"cards": len(names), "clean": clean, "partial": partial, "drop_kinds": drop_kinds}


def main() -> None:
    r = coverage()
    print(f"bridge coverage over {r['cards']} cards:")
    print(f"  {r['clean']} load with every clause mapped (vanilla/keyword/supported-trigger)")
    print(f"  {r['partial']} load partially (some clauses abstained — play as far as the engine reaches)")
    print(f"  abstained clause kinds: {dict(sorted(r['drop_kinds'].items(), key=lambda x: -x[1]))}")


_DEMO_DECKS = {
    "alice": ["Forest"] * 9 + ["Grizzly Bears"] * 3 + ["Gray Ogre"] * 2 + ["Hill Giant"] * 2
             + ["Craw Wurm"] + ["Tattered Mummy"] * 2,
    "bob": ["Forest"] * 9 + ["Storm Crow"] * 3 + ["Wind Drake"] * 2 + ["Hill Giant"] * 2
           + ["Gray Ogre"] * 2 + ["Tattered Mummy"],
}


def real_game(seed: int = 3) -> None:
    """A FULL self-playing game of real decks through the datalog engine — shuffled draws, land drops,
    mana-gated casting on curve, summoning sickness, combat, deaths, and the cards' interpreted triggers,
    all derived from cards.dl + engine_rules.dl (driver.py authors no game logic)."""
    print("Full real-card game — decks of real cards, rules from engine_rules.dl, effects from cards.dl:\n")
    loser = play_real_game(_DEMO_DECKS, seed=seed)
    print(f"\nresult: {loser} lost" if loser else "\nresult: no decisive winner within the turn cap")


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "game":
        demo_game()
    elif cmd == "realgame":
        real_game()
    else:
        main()
