"""test_zone_sort.py — the TYPED-PARTITION zone sort fold ('reveal the top N, put all <type> cards into
your hand and the rest on the bottom / in your graveyard'). It is the dig fold's typed sibling: _fold_dig
keeps a FIXED count M and bins the rest; _fold_zone_sort keeps EVERY revealed card matching a card-type /
subtype predicate. Covers the bridge fold (one zone_sort effect, no dropped clauses), the applier (correct
typed partition + rest routing), info-mode (revealed cards public, library order private), and the dig fold
staying intact (no regression). Run: python3 test_zone_sort.py"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)
import sim, bridge_to_engine as bridge, card_corpus, driver, effect_handlers
effect_handlers.load()

_ok = [0, 0]
def check(name, cond):
    _ok[0] += 1; _ok[1] += bool(cond)
    print(("  ok  " if cond else " FAIL"), name)

db = sim.load_db(); corpus = {c["name"]: c for c in card_corpus.load_cards()}
def _zs_rows(name):
    facts, dropped = bridge.card_facts(name, "alice", "x1", db, corpus)
    rows = [x for r in ("spell_effect", "trigger_effect", "activated_ability")
            for x in facts.get(r, ()) if "zone_sort" in x]
    drops = [d for d in dropped if d[0] == "effect"]
    return rows, drops

# --- interpreter -> bridge: each real shape folds to ONE zone_sort with no dropped clauses ----------------
# A1 (rest -> bottom), subtype filter, on a TRIGGERED ETB ability (Goblin Ringleader).
r, d = _zs_rows("Goblin Ringleader")
check("Goblin Ringleader (ETB): folds to zone_sort(4, subtype:goblin#bottom)",
      ("x1_a1", "zone_sort", 4, "subtype:goblin#bottom") in r)
check("Goblin Ringleader: no dropped effect clauses (reveal+put_on_bottom consumed)", not d)

# A1, a §205 card-TYPE disjunction filter, on a SPELL (Lair Delve: creature and land).
r, d = _zs_rows("Lair Delve")
check("Lair Delve (spell): folds to zone_sort(2, type:creature|land#bottom)",
      ("x1", "zone_sort", 2, "type:creature|land#bottom") in r and not d)

# A2 (rest -> graveyard): return_to_hand(all_..._revealed_this_way) + put_in_graveyard(the_rest) on a SPELL.
r, d = _zs_rows("Beast Hunt")
check("Beast Hunt (spell): folds to zone_sort(3, type:creature#graveyard)",
      ("x1", "zone_sort", 3, "type:creature#graveyard") in r and not d)
r, d = _zs_rows("Mulch")
check("Mulch (spell): folds to zone_sort(4, type:land#graveyard)",
      ("x1", "zone_sort", 4, "type:land#graveyard") in r and not d)

# --- ABSTAIN (faithful-or-abstain): a referent we can't bind must NOT fold -------------------------------
check("Brass Herald (chosen creature type): abstains (no zone_sort)", not _zs_rows("Brass Herald")[0])
check("Vigean Intuition (chosen type): abstains", not _zs_rows("Vigean Intuition")[0])
check("Vessel of Nascency (you-may a … from among them): abstains", not _zs_rows("Vessel of Nascency")[0])
check("Niv-Mizzet Reborn (the chosen cards): abstains", not _zs_rows("Niv-Mizzet Reborn")[0])
check("Torsten (any number … from among them — free count): abstains",
      not _zs_rows("Torsten, Founder of Benalia")[0])

# --- the applier partitions the looked-at top N (matching -> hand, rest -> bottom / graveyard) -----------
def _state():
    return {
        "is_player": {("alice",)},
        "in_library": {("alice", c) for c in ("c1", "c2", "c3", "c4", "c5")},
        "_lib_order": {"alice": ["c1", "c2", "c3", "c4", "c5"]},
        "printed_type": {("c1", "creature"), ("c2", "land"), ("c3", "creature"),
                         ("c4", "instant"), ("c5", "land")},
        "printed_subtype": {("c1", "goblin"), ("c3", "elf")},
        "in_hand": set(), "graveyard": set(), "revealed": set(),
    }

# type:creature, rest -> bottom: top 4 = c1,c2,c3,c4 -> creatures c1,c3 to hand; c2,c4 to bottom; c5 untouched.
s = _state()
effect_handlers.APPLY["zone_sort"](driver, s, "x1_a1", 4, "type:creature#bottom", "x1", "alice")
hand = {c for (p, c) in s["in_hand"] if p == "alice"}
lib = {c for (p, c) in s["in_library"] if p == "alice"}
check("zone_sort(type:creature, bottom): creatures c1,c3 -> hand", hand == {"c1", "c3"})
check("zone_sort: no card put into graveyard (rest -> bottom)", not s["graveyard"])
check("zone_sort: rest c2,c4 stay in library; c5 (un-looked) stays", lib == {"c2", "c4", "c5"})
check("zone_sort: bottom order has the binned cards at the end",
      s["_lib_order"]["alice"][-3:] == ["c5", "c2", "c4"])

# subtype:goblin, rest -> graveyard: top 3 = c1,c2,c3 -> only c1 (goblin) to hand; c2,c3 to graveyard.
s = _state()
effect_handlers.APPLY["zone_sort"](driver, s, "x1_a1", 3, "subtype:goblin#graveyard", "x1", "alice")
hand = {c for (p, c) in s["in_hand"] if p == "alice"}
gy = {c for (c,) in s["graveyard"]}
check("zone_sort(subtype:goblin, graveyard): only c1 -> hand", hand == {"c1"})
check("zone_sort(graveyard): c2,c3 -> graveyard", gy == {"c2", "c3"})
check("zone_sort: c2,c3 left the library", not ({("alice", "c2"), ("alice", "c3")} & s["in_library"]))

# the §205 type DISJUNCTION (creature|land) keeps cards of EITHER type.
s = _state()
effect_handlers.APPLY["zone_sort"](driver, s, "x1", 4, "type:creature|land#bottom", "x1", "alice")
hand = {c for (p, c) in s["in_hand"] if p == "alice"}
check("zone_sort(type:creature|land): c1,c2,c3 -> hand (c4 instant binned)", hand == {"c1", "c2", "c3"})

# --- INFO MODE: the looked-at cards become PUBLIC (revealed); library order beneath stays private --------
s = _state()
effect_handlers.APPLY["zone_sort"](driver, s, "x1", 4, "type:creature#bottom", "x1", "alice")
check("info: all 4 looked-at cards are marked revealed (public)",
      {c for (c,) in s["revealed"]} == {"c1", "c2", "c3", "c4"})
check("info: the un-looked c5 is NOT revealed (library order stays private)",
      ("c5",) not in s["revealed"])

# --- REGRESSION: the dig fold still folds dig cards to dig_to_hand (zone_sort didn't poach them) ----------
def _dig_rows(name):
    facts, _ = bridge.card_facts(name, "alice", "x1", db, corpus)
    return [r for r in facts.get("trigger_effect", ()) if r[1] == "dig_to_hand"]
check("dig regression: Organ Hoarder still folds to dig_to_hand(3, 1_graveyard)",
      ("x1_a0", "dig_to_hand", 3, "1_graveyard") in _dig_rows("Organ Hoarder"))
check("dig regression: a dig card produces NO zone_sort (folds are disjoint)",
      not _zs_rows("Organ Hoarder")[0])

print(f"\n{_ok[1]}/{_ok[0]} checks passed")
import sys; sys.exit(0 if _ok[1] == _ok[0] else 1)
