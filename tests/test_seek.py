"""test_seek.py — §701.x SEEK ('seek a [quality] card': put a matching library card into your hand WITHOUT
searching, WITHOUT revealing, and WITHOUT shuffling). Distinct from §701.18 search/tutor. Run with
MTG_NO_SPACY=1 python3 test_seek.py"""

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from interpreter import card_corpus
import sim, bridge_to_engine as bridge, driver, effect_handlers
from effect_handlers import library as L
effect_handlers.load()

_ok = [0, 0]
def check(name, cond):
    _ok[0] += 1; _ok[1] += bool(cond)
    print(("  ok  " if cond else " FAIL"), name)

print("library handler is in this worktree:", L.__file__)

# ── 1. ENCODER: faithful subset resolves, unevaluable qualities abstain ─────────────────────────────────
def enc(tgt, amt="-", extra="-"):
    return L._encode_seek("seek", amt, tgt, extra)

check("bare 'a nonland card' -> ('seek', 1, 'nonland')", enc("a_nonland_card") == ("seek", 1, "nonland"))
check("'a creature card' -> type:creature", enc("a_creature_card") == ("seek", 1, "type:creature"))
check("'a land card' -> any_land", enc("a_land_card") == ("seek", 1, "any_land"))
check("'two nonland cards' count=2", enc("two_nonland_cards") == ("seek", 2, "nonland"))
check("mana-value cap '… mv 3 or less'",
      enc("a_nonland_permanent_card_with_mana_value_3_or_less") == ("seek", 1, "nonland_permanent&mv<=3"))
check("type disjunction + cap",
      enc("an_instant_or_sorcery_card_with_mana_value_3_or_less") == ("seek", 1, "type:instant|sorcery&mv<=3"))
check("single subtype 'an elf card' -> csub:elf", enc("an_elf_card") == ("seek", 1, "csub:elf"))
check("'a basic land card' -> any_land", enc("a_basic_land_card") == ("seek", 1, "any_land"))
check("'a nonbasic land card' -> nonbasic_land", enc("a_nonbasic_land_card") == ("seek", 1, "nonbasic_land"))
check("basic-land subtype 'two forest cards' -> subtype:forest", enc("two_forest_cards") == ("seek", 2, "subtype:forest"))

# ABSTAIN: a quality the surfaced identity can't evaluate
for bad in ["a_card_with_greater_mana_value", "a_creature_card_of_the_most_prevalent_creature_type_in_your_library",
            "a_permanent_card_with_mana_value_equal_to_the_number_of_lands_you_control",
            "x_creature_enchantment_and_or_planeswalker_cards", "that_many_nonland_cards",
            "s_that_many_nonland_cards", "a_historic_card", "a_multicolored_card",
            "a_nonland_permanent_card_with_mana_value_x_or_less",
            "a_card_with_mana_value_equal_to_that_damage"]:
    check(f"ABSTAIN on {bad}", enc(bad) is None)
check("ABSTAIN on a non-modellable rider (extra != '-')",
      enc("an_instant_or_sorcery_card", extra="per_card_exiled_from_your_hand") is None)

# ── 2. APPLIER end-to-end: the matching card moves library->hand; NO shuffle/reveal/search event ─────────
db = sim.load_db(); corpus = {c["name"]: c for c in card_corpus.load_cards()}

def _materialized_state(cards):
    """A driver state with `cards` (name->tid) in the controller's library, printed identity surfaced."""
    state = {"in_library": set(), "instance_of": set(), "is_player": {("alice",)}, "in_hand": set()}
    for nm, tid in cards.items():
        f, _ = bridge.card_facts(nm, "alice", tid, db, corpus)
        for rel, rows in f.items():
            if isinstance(rows, set):
                state.setdefault(rel, set()).update(rows)
        state["in_library"].add(("alice", tid))
    state["_lib_order"] = {"alice": sorted(c for (p, c) in state["in_library"])}
    bridge._materialize_printed(state)
    return state

# library: a creature, a land, an instant, an artifact, an elf
cards = {"Grizzly Bears": "bears", "Forest": "forest", "Lightning Bolt": "bolt",
         "Mox Sapphire": "mox", "Llanowar Elves": "elf"}
s = _materialized_state(cards)
lib0 = len([1 for (p, _c) in s["in_library"] if p == "alice"])

# count shuffle/search-event side effects: monkeypatch to detect they are NOT invoked
fired = {"shuffle": 0, "search": 0}
_orig_shuf, _orig_fire = driver._shuffle_library, driver._fire_search_triggers
driver._shuffle_library = lambda st, p: fired.__setitem__("shuffle", fired["shuffle"] + 1)
driver._fire_search_triggers = lambda st, p: fired.__setitem__("search", fired["search"] + 1)
try:
    # seek a creature card -> the canonical-first creature ('bears' or 'elf') goes to hand
    effect_handlers.APPLY["seek"](driver, s, "x1", 1, "type:creature", "x1", "alice")
finally:
    driver._shuffle_library, driver._fire_search_triggers = _orig_shuf, _orig_fire

hand = {c for (p, c) in s["in_hand"] if p == "alice"}
lib1 = len([1 for (p, _c) in s["in_library"] if p == "alice"])
check("seek moved exactly one card to hand", len(hand) == 1)
check("the sought card is a CREATURE", any(t == "creature" for (c, t) in s["printed_type"] if c in hand))
check("library count dropped by exactly 1", lib1 == lib0 - 1)
check("the sought card left in_library", not (hand and ("alice", next(iter(hand))) in s["in_library"]))
check("the sought card left _lib_order", not (hand and next(iter(hand)) in s["_lib_order"]["alice"]))
check("NO shuffle side effect (seek != tutor)", fired["shuffle"] == 0)
check("NO search-event fired (seek is not a §701.18 search)", fired["search"] == 0)
check("no 'known'/reveal records created", not s.get("known") and not s.get("_known_top"))
check("nothing set aside in _searched (seek places directly, no destination clause)",
      not s.get("_searched", {}).get("alice"))

# ── 3. multi-count + fail-to-find + mv cap ──────────────────────────────────────────────────────────────
s = _materialized_state(cards)
effect_handlers.APPLY["seek"](driver, s, "x1", 2, "nonland", "x1", "alice")     # seek TWO nonland cards
hand = {c for (p, c) in s["in_hand"] if p == "alice"}
check("seek 2 nonland -> 2 cards to hand, none of them a land",
      len(hand) == 2 and not any(t == "land" for (c, t) in s["printed_type"] if c in hand))

s = _materialized_state({"Forest": "forest"})                    # only a land in the library
effect_handlers.APPLY["seek"](driver, s, "x1", 1, "type:creature", "x1", "alice")
check("fail-to-find (no creature) -> nothing to hand, land stays", not s["in_hand"] and ("alice", "forest") in s["in_library"])

# mana-value cap: with no surfaced mana_cost the cap can't be confirmed -> no match (conservative, like search)
s = _materialized_state(cards)
effect_handlers.APPLY["seek"](driver, s, "x1", 1, "type:creature&mv<=1", "x1", "alice")
check("mv cap with no surfaced mana_cost -> conservative no-match (faithful abstain)", not s["in_hand"])
# but WITH a surfaced mana value it matches within the cap
s = _materialized_state(cards); s.setdefault("mana_cost", set()).add(("bears", 2)); s["mana_cost"].add(("elf", 1))
effect_handlers.APPLY["seek"](driver, s, "x1", 1, "type:creature&mv<=1", "x1", "alice")
got = {c for (p, c) in s["in_hand"] if p == "alice"}
check("mv cap honored: seeks the elf (mv 1), not the bears (mv 2)", got == {"elf"})

# ── 4. end-to-end through card_facts on a real card (the spell path emits a seek spell_effect) ───────────
def _seek_rows(name):
    facts, dropped = bridge.card_facts(name, "alice", "x1", db, corpus)
    return [r for r in facts.get("spell_effect", ()) if r[1] == "seek"], dropped

rows, dropped = _seek_rows("Seek New Knowledge")                 # 'Seek two nonland cards' (then draw one)
check("Seek New Knowledge: emits a seek spell_effect (no longer dropped)",
      any(r[1] == "seek" and r[2] == 2 and r[3] == "nonland" for r in rows))
check("Seek New Knowledge: 'seek' no longer in dropped", ("effect", "seek") not in dropped)

print(f"\n{_ok[1]}/{_ok[0]} checks passed")
import sys; sys.exit(0 if _ok[1] == _ok[0] else 1)
