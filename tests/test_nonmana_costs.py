"""test_nonmana_costs.py — §602.5 NON-MANA activated COSTS: 'Sacrifice a <creature/artifact/subtype>',
'Discard a card', 'Pay N life' are now OFFERED (env.legal_actions) and PAID (driver / env.step) as activation
costs, with the cost choice (which creature to sac / which card to discard) routed through the _choose seam so
it is policy-drivable and correct in imperfect information. Verified in BOTH perfect and imperfect info.
Run: python3 test_nonmana_costs.py"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)
from interpreter import card_corpus
import sim, bridge_to_engine as bridge, driver, env, observe, effect_handlers
effect_handlers.load()

_ok = [0, 0]
def check(name, cond):
    _ok[0] += 1; _ok[1] += bool(cond)
    print(("  ok  " if cond else " FAIL"), name)

# ============ 1. the PARSER — clean shapes parse, everything else abstains (faithful-or-abstain) ============
nm = bridge._nonmana_cost
check("'Sacrifice a creature' -> sac_filter=creature", nm("Sacrifice a creature")["sac_filter"] == "creature")
check("'{1}, Sacrifice a creature' rides a {1} mana part",
      nm("{1}, Sacrifice a creature") == {"mana": 1, "taps": False, "sac_self": False, "sac_filter": "creature", "life": 0, "discard": 0})
check("'Sacrifice an artifact' -> sac_filter=artifact", nm("Sacrifice an artifact")["sac_filter"] == "artifact")
check("'Sacrifice a land' -> sac_filter=land", nm("Sacrifice a land")["sac_filter"] == "land")
check("'Sacrifice a Saproling' -> subtype:saproling", nm("Sacrifice a Saproling")["sac_filter"] == "subtype:saproling")
check("'Sacrifice another creature' -> another_creature", nm("Sacrifice another creature")["sac_filter"] == "another_creature")
check("'Sacrifice ~' -> sac_self", nm("Sacrifice ~")["sac_self"] is True)
check("'Discard a card' -> discard=1", nm("Discard a card")["discard"] == 1)
check("'{T}, Discard a card' rides {T}", nm("{T}, Discard a card") == {"mana": 0, "taps": True, "sac_self": False, "sac_filter": None, "life": 0, "discard": 1})
check("'Discard two cards' -> discard=2", nm("Discard two cards")["discard"] == 2)
check("'Pay 1 life' -> life=1", nm("Pay 1 life")["life"] == 1)
check("'{T}, Pay 1 life' rides {T}", nm("{T}, Pay 1 life") == {"mana": 0, "taps": True, "sac_self": False, "sac_filter": None, "life": 1, "discard": 0})
# ABSTAIN: variable cost, unmodeled non-mana, double sacrifice
check("'{X}, Sacrifice a creature' abstains (variable)", nm("{X}, Sacrifice a creature") is None)
check("'Exile ~ from your hand' abstains (unmodeled)", nm("Exile ~ from your hand") is None)
check("'Remove three quest counters... and sacrifice it' abstains", nm("Remove three quest counters from ~ and sacrifice it") is None)
check("None cost abstains", nm(None) is None)

# ============ 2. the BRIDGE emits the cost facts on real cards ============
db = sim.load_db(); corpus = {c["name"]: c for c in card_corpus.load_cards()}
def _facts(name):
    f, dropped = bridge.card_facts(name, "alice", "x1", db, corpus)
    return f, dropped
f, dropped = _facts("Bloodflow Connoisseur")    # 'Sacrifice a creature: put a +1/+1 counter on this'
check("Bloodflow Connoisseur: ability_sac_filter=creature", ("x1_a0", "creature") in f.get("ability_sac_filter", set()))
check("Bloodflow Connoisseur: its activated_ability is modeled (no longer dropped)", bool(f.get("activated_ability")))
check("Bloodflow Connoisseur: cost no longer in dropped activated_cost bucket",
      not any(d[0] == "activated_cost" for d in dropped))
f, _ = _facts("Elvish Farmer")                  # 'Sacrifice a Saproling: gain 2 life'
check("Elvish Farmer: ability_sac_filter=subtype:saproling", ("x1_a2", "subtype:saproling") in f.get("ability_sac_filter", set()))
f, _ = _facts("Griselbrand")                     # 'Pay 7 life: Draw seven cards'
check("Griselbrand: ability_life_cost=7", ("x1_a1", 7) in f.get("ability_life_cost", set()))
f, _ = _facts("Psychic Frog")                    # 'Discard a card: gets +1/+1 ...'
check("Psychic Frog: ability_discard_cost=1", ("x1_a1", 1) in f.get("ability_discard_cost", set()))
f, _ = _facts("Zombie Infestation")              # 'Discard two cards: create a 2/2 Zombie'
check("Zombie Infestation: ability_discard_cost=2", ("x1_a0", 2) in f.get("ability_discard_cost", set()))

# ============ helpers to build a battlefield from real cards ============
def _place(state, name, pl, tid, zone="on_battlefield"):
    fa, _ = bridge.card_facts(name, pl, tid, db, corpus)
    for rel, rows in fa.items():
        state.setdefault(rel, set()).update(rows)
    state.setdefault(zone, set()).add((tid,) if zone != "in_hand" else (pl, tid))
    return tid

def _base(mana=5):
    return {"is_player": {("alice",), ("bob",)}, "life": {("alice", 20), ("bob", 20)},
            "active_player": {("alice",)}, "current_step": {("precombat_main",)},
            "has_priority": {("alice",)}, "mana_available": {("alice", mana), ("bob", 0)},
            "counter": set(), "tapped": set(), "_sick": set(), "in_hand": set(), "graveyard": set()}

# ============ 3. PERFECT INFO — SACRIFICE a creature (Bloodflow Connoisseur) ============
s = _base()
bc = _place(s, "Bloodflow Connoisseur", "alice", "bc")
gb = _place(s, "Grizzly Bears", "alice", "gb")
bridge._materialize_printed(s)
usable = driver._activatable(s, "alice")
check("perfect: Bloodflow's sac ability is activatable (a creature exists to sac)", bool(usable))
seen = {}
def _spy(state, key, opts, default):
    seen[key] = (list(opts) if opts is not None else None, default); return default
_orig = driver._choose; driver._choose = _spy
try:
    driver._activate_phase(s, "alice", ["alice", "bob"])
finally:
    driver._choose = _orig
check("perfect: the sacrifice choice routed through the _choose seam (key='sacrifice')", "sacrifice" in seen)
check("perfect: both alice's creatures are offered as sac options", set(seen["sacrifice"][0]) == {bc, gb})
check("perfect: greedy default sacs the OTHER creature (gb), not the ability's source", seen["sacrifice"][1] == gb)
check("perfect: the sacrificed creature (gb) went to the graveyard", (gb,) in s["graveyard"])
check("perfect: Bloodflow got its +1/+1 counter from resolving the ability", (bc, "p1p1", 1) in s["counter"])

# with NO spare creature, the only sac option is the source itself (rules-legal: sac the source to its own ability)
s2 = _base()
bc2 = _place(s2, "Bloodflow Connoisseur", "alice", "bc")
bridge._materialize_printed(s2)
check("perfect: with only the source, sac is still activatable (can sac itself)", bool(driver._activatable(s2, "alice")))

# ============ 4. PERFECT INFO — SACRIFICE a subtype (Elvish Farmer: sac a Saproling, gain 2 life) ============
s = _base()
ef = _place(s, "Elvish Farmer", "alice", "ef")
# give alice a Saproling token via the engine's token machinery (1/1 green Saproling)
driver._create_token(s, "1_1_green_saproling_creature", "alice", 1)
sap = next(c for (c,) in s["on_battlefield"] if c.startswith("1_1_green_saproling"))
bridge._materialize_printed(s)
usable = [u for u in driver._activatable(s, "alice") if ("ef_a2", "subtype:saproling") in s.get("ability_sac_filter", set()) and u[0] == "ef_a2"]
check("perfect: Elvish Farmer's 'Sacrifice a Saproling' is activatable (a Saproling exists)", bool(usable))
life0 = next(v for (p, v) in s["life"] if p == "alice")
row = next(u for u in driver._activatable(s, "alice") if u[0] == "ef_a2")
# pin the choice so we only sac the Saproling
driver._choose = lambda st, k, o, d: (sap if k == "sacrifice" else (row if k == "activate" else d))
try:
    driver._activate_phase(s, "alice", ["alice", "bob"])
finally:
    driver._choose = _orig
check("perfect: the Saproling was sacrificed", (sap,) in s["graveyard"])
check("perfect: alice gained 2 life from the ability", next(v for (p, v) in s["life"] if p == "alice") == life0 + 2)

# Elvish Farmer with NO Saproling -> the sac-a-Saproling ability is NOT offered (faithful affordability gate)
s3 = _base()
_place(s3, "Elvish Farmer", "alice", "ef")
bridge._materialize_printed(s3)
check("perfect: Elvish Farmer with no Saproling -> its sac-Saproling ability is NOT activatable",
      not any(u[0] == "ef_a2" for u in driver._activatable(s3, "alice")))

# ============ 5. PERFECT INFO — PAY N LIFE (Griselbrand: Pay 7 life, draw 7) ============
s = _base(mana=0); s["in_library"] = {("alice", f"L{i}") for i in range(10)}; s["_lib_order"] = {"alice": [f"L{i}" for i in range(10)]}
gr = _place(s, "Griselbrand", "alice", "gr")
bridge._materialize_printed(s)
check("perfect: Griselbrand's 'Pay 7 life' ability is activatable at 20 life", any(u[1] == "gr" for u in driver._activatable(s, "alice")))
hand0 = len([1 for (p, _c) in s["in_hand"] if p == "alice"])
row = next(u for u in driver._activatable(s, "alice") if u[1] == "gr")
driver._choose = lambda st, k, o, d: (row if k == "activate" else d)
try:
    driver._activate_phase(s, "alice", ["alice", "bob"])
finally:
    driver._choose = _orig
check("perfect: alice paid 7 life (20 -> 13)", next(v for (p, v) in s["life"] if p == "alice") == 13)
check("perfect: alice drew 7 cards", len([1 for (p, _c) in s["in_hand"] if p == "alice"]) == hand0 + 7)
# at 7 or fewer life the cost is unpayable (§118.4 — can't reduce yourself to <1 to pay a life cost)
s4 = _base(mana=0); _place(s4, "Griselbrand", "alice", "gr"); s4["life"] = {("alice", 7), ("bob", 20)}
bridge._materialize_printed(s4)
check("perfect: at 7 life, 'Pay 7 life' is NOT activatable (would leave you at 0)",
      not any(u[1] == "gr" for u in driver._activatable(s4, "alice")))

# ============ 6. ENV ACTION SURFACE — the ability is OFFERED and step() PAYS the non-mana cost ============
s = _base()
bc = _place(s, "Bloodflow Connoisseur", "alice", "bc")
gb = _place(s, "Grizzly Bears", "alice", "gb")
bridge._materialize_printed(s)
acts = env.legal_actions(s)
sac_acts = [a for a in acts if a[0] == "activate" and a[2][0] == "bc_a0"]
check("env: the 'Sacrifice a creature' ability appears in legal_actions", bool(sac_acts))
# step it (greedy default sacs gb) — a PURE transition that pays the sacrifice cost
s_after = env.step(s, sac_acts[0])
check("env.step: a creature was sacrificed to the graveyard", len(s_after.get("graveyard", set())) == 1)
check("env.step: the original state is untouched (pure transition)", not s.get("graveyard"))
check("env.step: Bloodflow received its +1/+1 counter after the activation resolved",
      ("bc", "p1p1", 1) in s_after.get("counter", set()))

# ============ 7. IMPERFECT INFO — the DISCARD cost choice is over the player's OWN (hidden) hand ============
# Zombie Infestation: 'Discard two cards: create a 2/2 black Zombie creature token'. The discard CHOICE is over
# alice's PRIVATE hand; the resulting graveyard + Zombie token are PUBLIC. observe(state, seat) projects each
# seat's view — alice sees her own hand, bob sees only a count.
def _zombie_state():
    s = _base()
    _place(s, "Zombie Infestation", "alice", "zi")
    for i, nm_ in enumerate(["Grizzly Bears", "Hill Giant", "Craw Wurm"]):
        _place(s, nm_, "alice", f"h{i}", zone="in_hand")     # three cards in hand to discard two of
    bridge._materialize_printed(s)
    return s
s = _zombie_state()
hand_ids = sorted(c for (p, c) in s["in_hand"] if p == "alice")
check("imperfect: alice has three cards in hand to discard from", len(hand_ids) == 3)
check("imperfect: Zombie Infestation's 'Discard two cards' ability is activatable (>=2 cards in hand)",
      any(u[1] == "zi" for u in driver._activatable(s, "alice")))
# the discard cost choice is observable on the _choose seam, over alice's OWN hand
seen.clear()
def _spy2(state, key, opts, default):
    seen.setdefault(key, (list(opts) if opts is not None else None, default))
    if key == "activate":
        return next(o for o in opts if o is not None and o[1] == "zi")
    return default
driver._choose = _spy2
try:
    driver._activate_phase(s, "alice", ["alice", "bob"])
finally:
    driver._choose = _orig
check("imperfect: the discard choice routed through _choose (key='discard')", "discard" in seen)
check("imperfect: the discard options were alice's OWN hand cards", set(seen["discard"][0]) <= set(hand_ids))
check("imperfect: two cards were discarded to the (public) graveyard", len(s.get("graveyard", set())) == 2)
check("imperfect: one card is left in alice's hand (3 - 2 discarded)",
      len([1 for (p, _c) in s["in_hand"] if p == "alice"]) == 1)
zombie = next((c for (c,) in s.get("on_battlefield", set()) if c.startswith("2_2_black_zombie")), None)
check("imperfect: a 2/2 Zombie token was created (public result of the ability)", zombie is not None)

# project the post-state to each seat: graveyard + token are public; alice's remaining hand is private to alice
va = observe.observe(s, "alice"); vb = observe.observe(s, "bob")
gy = s["graveyard"]
check("imperfect: alice sees the discarded cards in the public graveyard", gy <= va.get("graveyard", set()))
check("imperfect: bob ALSO sees the discarded cards (graveyard is public)", gy <= vb.get("graveyard", set()))
check("imperfect: bob ALSO sees the Zombie token (public token)", (zombie,) in vb.get("on_battlefield", set()))
bob_hand_count = next((n for (p, n) in vb.get("hand_count", set()) if p == "alice"), None)
check("imperfect: bob does NOT see alice's remaining hand card (only a count of 1)",
      not any(p == "alice" for (p, _c) in vb.get("in_hand", set())) and bob_hand_count == 1)
check("imperfect: alice DOES see her own remaining hand card", any(p == "alice" for (p, _c) in va.get("in_hand", set())))

# the discard cost resolves IDENTICALLY whether driven on the full state or alice's OWN observed view
def _run_discard(state):
    st = driver.clone_state(state)
    row = next(u for u in driver._activatable(st, "alice") if u[1] == "zi")
    driver._choose = lambda s_, k_, o_, d_: (row if k_ == "activate" else (sorted(o_)[0] if k_ == "discard" else d_))
    try:
        driver._activate_phase(st, "alice", ["alice", "bob"])
    finally:
        driver._choose = _orig
    return len(st.get("graveyard", set()))
s_full = _zombie_state()
full_gy = _run_discard(s_full)
obs = observe.observe(s_full, "alice")                      # alice CAN see her own hand in her own view
check("imperfect: discard cost resolves the same on alice's OWN observed view as on the full state",
      _run_discard(obs) == full_gy == 2)

print(f"\n{_ok[1]}/{_ok[0]} checks passed")
import sys; sys.exit(0 if _ok[1] == _ok[0] else 1)
