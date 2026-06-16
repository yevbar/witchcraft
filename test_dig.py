"""test_dig.py — the look-and-bin dig idiom ('look at the top N, put M into your hand, the rest into your
graveyard') on TRIGGERED abilities, now folded through the same _fold_dig + dig_to_hand architecture the
spell/activated paths already use. Run: python3 test_dig.py"""
import sim, bridge_to_engine as bridge, card_corpus, driver, effect_handlers
effect_handlers.load()

_ok = [0, 0]
def check(name, cond):
    _ok[0] += 1; _ok[1] += bool(cond)
    print(("  ok  " if cond else " FAIL"), name)

# --- interpreter -> bridge link: a TRIGGERED dig now emits one dig_to_hand trigger_effect ---------------
db = sim.load_db(); corpus = {c["name"]: c for c in card_corpus.load_cards()}
def _dig_rows(name):
    facts, _ = bridge.card_facts(name, "alice", "x1", db, corpus)
    return [r for r in facts.get("trigger_effect", ()) if r[1] == "dig_to_hand"]

check("Organ Hoarder (ETB): folds to dig_to_hand(3, 1_graveyard)",
      ("x1_a0", "dig_to_hand", 3, "1_graveyard") in _dig_rows("Organ Hoarder"))
check("Tower Geist (ETB): folds to dig_to_hand(2, 1_graveyard)",
      ("x1_a1", "dig_to_hand", 2, "1_graveyard") in _dig_rows("Tower Geist"))
check("a triggered dig no longer drops its put_in_hand/put_in_graveyard clauses",
      len(_dig_rows("Organ Hoarder")) == 1)

# --- the applier resolves the folded effect (source-agnostic; same one the spell path uses) -------------
def _state():
    return {
        "is_player": {("alice",)},
        "in_library": {("alice", "c1"), ("alice", "c2"), ("alice", "c3"), ("alice", "c4")},
        "_lib_order": {"alice": ["c1", "c2", "c3", "c4"]},
        "in_hand": set(), "graveyard": set(),
    }

s = _state()
effect_handlers.APPLY["dig_to_hand"](driver, s, "x1_a0", 3, "1_graveyard", "x1", "alice")
hand = {c for (p, c) in s["in_hand"] if p == "alice"}
gy = {c for (c,) in s["graveyard"]}
lib = {c for (p, c) in s["in_library"] if p == "alice"}
check("dig: exactly 1 card put into hand", len(hand) == 1)
check("dig: exactly 2 cards put into graveyard (the rest of the top 3)", len(gy) == 2)
check("dig: the 4th (un-looked) card stays in the library", lib == {"c4"})
check("dig: hand + graveyard came from the top 3 looked-at", hand | gy == {"c1", "c2", "c3"})

# the 'rest to bottom' variant (dest != graveyard) keeps the rest in the library
s = _state()
effect_handlers.APPLY["dig_to_hand"](driver, s, "x1_a0", 3, "1_bottom", "x1", "alice")
check("dig-to-bottom: 1 to hand, the rest stay in library (binned to bottom)",
      len({c for (p, c) in s["in_hand"]}) == 1 and not s["graveyard"])

print(f"\n{_ok[1]}/{_ok[0]} checks passed")
import sys; sys.exit(0 if _ok[1] == _ok[0] else 1)
