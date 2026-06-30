"""test_dynamic_amount.py — STRUCTURAL #3: DYNAMIC ('for each') player-scoped amounts. A draw / gain_life /
lose_life / mill whose amount is a clean count slug ('N_per_<X>' or 'equal_to_the_number_of_<X>') resolves
to base*<live count> at resolution time (driver._dyn_count). Covers the recognized count dimensions, the
SPELL path (spell_dyn_effect) and the TRIGGER path (pending_dyn), doubler interaction, and BOTH info modes.
Run: python3 test_dynamic_amount.py"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)
from interpreter import card_corpus
import sim, bridge_to_engine as bridge, driver, observe, effect_handlers
effect_handlers.load()

_ok = [0, 0]
def check(name, cond):
    _ok[0] += 1; _ok[1] += bool(cond)
    print(("  ok  " if cond else " FAIL"), name)

db = sim.load_db(); corpus = {c["name"]: c for c in card_corpus.load_cards()}

def _facts(name, ctrl, tid, into):
    f, _ = bridge.card_facts(name, ctrl, tid, db, corpus)
    for rel, rows in f.items():
        into.setdefault(rel, set()).update(rows)

def _base_state():
    return {"is_player": {("alice",), ("bob",)}, "life": {("alice", 20), ("bob", 20)},
            "in_library": set(), "in_hand": set()}

def _add_creatures(st, ctrl, n, card="Grizzly Bears", prefix="cr"):
    for i in range(n):
        c = f"{prefix}{ctrl}{i}"
        _facts(card, ctrl, c, st)
        st.setdefault("on_battlefield", set()).add((c,))

def _hand(st, p):
    return sum(1 for (q, _c) in st.get("in_hand", set()) if q == p)
def _life(st, p):
    return next(v for (q, v) in st["life"] if q == p)

# ============================================================ the engine derives the count slugs ===========
print("--- engine derivation: dyn_amount_tag table + spell_dyn_effect / trigger_dyn_effect ---")
from interpreter import build_engine as be
tagged = {s for (s, _b, _t) in be._DYN_AMOUNT_TAG}
check("dyn_amount_tag covers '1_per_creature_you_control'", "1_per_creature_you_control" in tagged)
check("dyn_amount_tag covers 'equal_to_the_number_of_cards_in_your_hand'", "equal_to_the_number_of_cards_in_your_hand" in tagged)

st = _base_state(); _facts("Collective Unconscious", "alice", "cu", st); _add_creatures(st, "alice", 3)
sde = sorted(r for r in driver.run(st, ["spell_dyn_effect"])["spell_dyn_effect"] if r[0] == "cu")
check("Collective Unconscious -> spell_dyn_effect(draw, base 1, creature_yc)",
      sde == [("cu", "draw", "1", "creature_yc", "controller")])

# ============================================================ _dyn_count per dimension =====================
print("--- _dyn_count: each recognized count dimension ---")
st = _base_state(); _add_creatures(st, "alice", 4); _add_creatures(st, "bob", 2)
check("creature_yc counts only YOUR creatures (4, not bob's)", driver._dyn_count(st, "creature_yc", "alice") == 4)
check("creature_yc for bob = 2", driver._dyn_count(st, "creature_yc", "bob") == 2)

st = _base_state()
for i in range(3):
    _facts("Sol Ring", "alice", f"art{i}", st); st.setdefault("on_battlefield", set()).add((f"art{i}",))
check("artifact_yc counts your artifacts (3)", driver._dyn_count(st, "artifact_yc", "alice") == 3)

st = _base_state()
for i in range(5):
    _facts("Forest", "alice", f"ld{i}", st); st.setdefault("on_battlefield", set()).add((f"ld{i}",))
check("land_yc counts your lands (5)", driver._dyn_count(st, "land_yc", "alice") == 5)

st = _base_state(); st["in_hand"] = {("alice", "h1"), ("alice", "h2"), ("alice", "h3"), ("bob", "hb")}
check("cards_in_hand counts your hand (3)", driver._dyn_count(st, "cards_in_hand", "alice") == 3)

st = _base_state()
for i in range(2):                                            # creature cards in alice's graveyard
    _facts("Grizzly Bears", "alice", f"gy{i}", st); st.setdefault("graveyard", set()).add((f"gy{i}",))
_facts("Lightning Bolt", "alice", "gyspell", st); st.setdefault("graveyard", set()).add(("gyspell",))  # not a creature
_facts("Grizzly Bears", "bob", "gybob", st); st.setdefault("graveyard", set()).add(("gybob",))          # bob's
check("creature_cards_in_gy: 2 (alice's creature cards only; spell + bob's excluded)",
      driver._dyn_count(st, "creature_cards_in_gy", "alice") == 2)
check("unrecognized tag abstains -> 0", driver._dyn_count(st, "nonsense_tag", "alice") == 0)

# ============================================================ SPELL PATH ====================================
print("--- spell path: a resolving spell scales by the live count ---")
# Collective Unconscious — draw 1 per creature you control. 3 creatures -> draw 3.
st = _base_state(); _facts("Collective Unconscious", "alice", "cu", st); _add_creatures(st, "alice", 3)
st["in_library"] = {("alice", f"deck{i}") for i in range(10)}
driver._run_spell_dyn(st, "cu", "alice")
check("Collective Unconscious draws 3 (one per creature)", _hand(st, "alice") == 3)

st = _base_state(); _facts("Collective Unconscious", "alice", "cu", st)  # 0 creatures
st["in_library"] = {("alice", f"deck{i}") for i in range(10)}
driver._run_spell_dyn(st, "cu", "alice")
check("Collective Unconscious with 0 creatures draws 0 (no crash)", _hand(st, "alice") == 0)

# Ancient Excavation — draw equal to the number of cards in your hand (base 1).
st = _base_state(); _facts("Ancient Excavation", "alice", "ae", st)
st["in_hand"] = {("alice", "x1"), ("alice", "x2")}           # 2 cards in hand -> draw 2
st["in_library"] = {("alice", f"deck{i}") for i in range(10)}
driver._run_spell_dyn(st, "ae", "alice")
check("Ancient Excavation draws 2 (cards in hand)", _hand(st, "alice") == 2 + 2)

# Elemental Spectacle — gain life equal to creatures you control.
st = _base_state(); _facts("Elemental Spectacle", "alice", "es", st); _add_creatures(st, "alice", 3)
driver._run_spell_dyn(st, "es", "alice")
check("Elemental Spectacle gains 3 life (creatures you control) -> 23", _life(st, "alice") == 23)

# Gruesome Fate — EACH OPPONENT loses life equal to creatures you control (scope = each_opponent; count = YOURS).
st = _base_state(); _facts("Gruesome Fate", "alice", "gf", st); _add_creatures(st, "alice", 4)
driver._run_spell_dyn(st, "gf", "alice")
check("Gruesome Fate: each opponent loses 4 (alice's creature count); bob -> 16", _life(st, "bob") == 16)
check("Gruesome Fate doesn't drain the caster", _life(st, "alice") == 20)

# ============================================================ TRIGGER PATH ==================================
print("--- trigger path: pending_dyn fires alongside pending ---")
# Angel of Renewal — 'When ~ enters, you gain 1 life per creature you control' (ETB, counts itself + others).
st = _base_state(); _facts("Angel of Renewal", "alice", "aor", st); st.setdefault("on_battlefield", set()).add(("aor",))
_add_creatures(st, "alice", 2)
# stage the ETB look-back the engine's etb trigger needs
st["just_entered"] = {("aor",)}
pend, pend_dyn = driver._pending_both(st)
check("Angel of Renewal produces a pending_dyn gain_life row",
      any(e == "gain_life" and tag == "creature_yc" for (_a, e, _b, tag, _t, _s, _c) in pend_dyn))
driver._apply_effects(st, pend, pend_dyn)
# 2 bears + the angel itself = 3 creatures -> gain 3 -> 23
check("Angel of Renewal ETB gains 3 (2 others + itself) -> 23", _life(st, "alice") == 23)

# Venser's Journal — 'At the beginning of your upkeep, you gain 1 life per card in your hand.'
st = _base_state(); _facts("Venser's Journal", "alice", "vj", st); st.setdefault("on_battlefield", set()).add(("vj",))
st["in_hand"] = {("alice", "c1"), ("alice", "c2"), ("alice", "c3")}
st["current_step"] = {("upkeep",)}; st["active_player"] = {("alice",)}
pend, pend_dyn = driver._pending_both(st)
driver._apply_effects(st, pend, pend_dyn)
check("Venser's Journal upkeep gains 3 (cards in hand) -> 23", _life(st, "alice") == 23)

# ============================================================ DOUBLER INTERACTION ==========================
print("--- doubler interaction: the scaled gain still flows through _adjust_life replacements ---")
# Elemental Spectacle (gain = creatures you control = 3) under Alhammarret's Archive (life-gain doubler).
st = _base_state(); _facts("Elemental Spectacle", "alice", "es", st); _add_creatures(st, "alice", 3)
_facts("Alhammarret's Archive", "alice", "arch", st); st.setdefault("on_battlefield", set()).add(("arch",))
st.setdefault("life_repl", set()).add(("alhammarret_s_archive", "double"))
driver._run_spell_dyn(st, "es", "alice")
check("dyn gain 3 doubled by Alhammarret's Archive -> +6 (life 26)", _life(st, "alice") == 26)

# ============================================================ BOTH INFO MODES ===============================
print("--- both info modes: the dyn count is computed on the TRUE state; results are public ---")
# A dyn draw resolved: alice's hand grows (private to alice); bob sees only a hand_count, never the cards.
st = _base_state(); _facts("Collective Unconscious", "alice", "cu", st); _add_creatures(st, "alice", 2)
st["in_library"] = {("alice", f"deck{i}") for i in range(10)}
driver._run_spell_dyn(st, "cu", "alice")
check("perfect info: alice drew 2 (one per creature)", _hand(st, "alice") == 2)
va = observe.observe(st, "alice"); vb = observe.observe(st, "bob")
check("imperfect info: alice sees her own drawn cards", _hand(va, "alice") == 2)
check("imperfect info: bob sees alice's hand COUNT, not the cards",
      ("alice", 2) in vb.get("hand_count", set()) and not any(p == "alice" for (p, _c) in vb.get("in_hand", set())))

# A dyn life loss is public board state — both seats agree.
st = _base_state(); _facts("Gruesome Fate", "alice", "gf", st); _add_creatures(st, "alice", 3)
driver._run_spell_dyn(st, "gf", "alice")
vb = observe.observe(st, "bob")
check("imperfect info: bob's own life loss (17) is visible to bob", _life(vb, "bob") == 17)
check("imperfect info: alice also sees bob at 17 (public life)", _life(observe.observe(st, "alice"), "bob") == 17)

# A dyn count over a HIDDEN zone (cards in your hand) is still computed correctly on the true state when the
# CONTROLLER resolves (the referee runs on the true state; the controller knows their own hand).
st = _base_state(); _facts("Elemental Spectacle", "alice", "es", st); _add_creatures(st, "alice", 1)
driver._run_spell_dyn(st, "es", "alice")
check("dyn over your own board resolves the same regardless of observer (gain 1 -> 21)", _life(st, "alice") == 21)

print(f"\n{_ok[1]}/{_ok[0]} checks passed")
import sys; sys.exit(0 if _ok[1] == _ok[0] else 1)
