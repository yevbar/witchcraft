"""test_static_consumers.py — §604 continuous PLAYER-permission consumers wired from the inert
`static_player` family: 'no_maximum_hand_size' (cleanup discard skipped) + 'players/opponents_cant_gain_life'
(life-gain prevention). Verified in BOTH perfect and imperfect information. Run: python3 test_static_consumers.py"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)
from interpreter import card_corpus
import sim, bridge_to_engine as bridge, driver, observe, effect_handlers
effect_handlers.load()

_ok = [0, 0]
def check(name, cond):
    _ok[0] += 1; _ok[1] += bool(cond)
    print(("  ok  " if cond else " FAIL"), name)

# --- interpreter -> bridge: the real cards emit the driver-only static_player facts -----------------
db = sim.load_db(); corpus = {c["name"]: c for c in card_corpus.load_cards()}
def _sp(name):
    facts, _ = bridge.card_facts(name, "alice", "x1", db, corpus)
    return facts.get("static_player", set())
check("Reliquary Tower emits no_maximum_hand_size", ("reliquary_tower", "no_maximum_hand_size") in _sp("Reliquary Tower"))
check("The Lux Foundation Library emits players_no_maximum_hand_size",
      ("the_lux_foundation_library", "players_no_maximum_hand_size") in _sp("The Lux Foundation Library"))
check("Witch Hunt emits players_cant_gain_life", ("witch_hunt", "players_cant_gain_life") in _sp("Witch Hunt"))
check("Tibalt emits opponents_cant_gain_life",
      ("tibalt_rakish_instigator", "opponents_cant_gain_life") in _sp("Tibalt, Rakish Instigator"))

# ============================ #1 no_maximum_hand_size -> cleanup discard ============================
def _hand_state(n_cards, statics=()):
    """alice with n_cards in hand + bob; `statics` = list of (controller, slug, param) permanents."""
    s = {"is_player": {("alice",), ("bob",)}, "active_player": {("alice",)},
         "life": {("alice", 20), ("bob", 20)}, "in_hand": set(), "on_battlefield": set(),
         "printed_control": set(), "instance_of": set(), "static_player": set(),
         "graveyard": set(), "exile": set(), "current_step": {("cleanup",)}}
    for i in range(n_cards):
        s["in_hand"].add(("alice", f"h{i}"))
    for j, (ctrl, slug, param) in enumerate(statics):
        c = f"perm{j}"; s["on_battlefield"].add((c,)); s["printed_control"].add((ctrl, c))
        s["instance_of"].add((c, slug)); s["static_player"].add((slug, param))
    return s
def _hand(s, p):
    return sorted(c for (pp, c) in s.get("in_hand", set()) if pp == p)

# baseline: no static -> discard down to 7 at cleanup
s = _hand_state(10); driver._cleanup_discard(s, "alice")
check("no static: 10-card hand discards down to 7", len(_hand(s, "alice")) == 7)
check("no static: the 3 discarded cards reach the graveyard", len(s["graveyard"]) == 3)
# at/under the cap -> nothing discarded
s = _hand_state(5); driver._cleanup_discard(s, "alice")
check("no static: 5-card hand is untouched", len(_hand(s, "alice")) == 5 and not s["graveyard"])

# Reliquary Tower (no_maximum_hand_size) the active player controls -> NO discard
s = _hand_state(10, [("alice", "reliquary_tower", "no_maximum_hand_size")])
driver._cleanup_discard(s, "alice")
check("Reliquary Tower: alice keeps her whole 10-card hand", len(_hand(s, "alice")) == 10 and not s["graveyard"])
# the OPPONENT's Reliquary Tower does NOT exempt the active player
s = _hand_state(10, [("bob", "reliquary_tower", "no_maximum_hand_size")])
driver._cleanup_discard(s, "alice")
check("opponent's Reliquary Tower doesn't exempt alice (discards to 7)", len(_hand(s, "alice")) == 7)
# players_no_maximum_hand_size (Lux Foundation Library) on ANYONE's permanent exempts everyone
s = _hand_state(10, [("bob", "the_lux_foundation_library", "players_no_maximum_hand_size")])
driver._cleanup_discard(s, "alice")
check("players_no_maximum_hand_size exempts the active player too", len(_hand(s, "alice")) == 10)
# static_player not loaded at all -> classic discard-to-7 still works
s = _hand_state(9); s.pop("static_player"); driver._cleanup_discard(s, "alice")
check("static_player unloaded: still discards to 7", len(_hand(s, "alice")) == 7)

# _end_of_turn calls the cleanup discard for the active player
s = _hand_state(9); driver._end_of_turn(s)
check("_end_of_turn enforces the cap (9 -> 7)", len(_hand(s, "alice")) == 7)

# imperfect info: the discard is the active player's CHOICE over her OWN visible hand; the result count is
# public (opp sees hand_count), but opp never sees alice's card identities.
s = _hand_state(10); driver._cleanup_discard(s, "alice")
va = observe.observe(s, "alice"); vb = observe.observe(s, "bob")
check("imperfect info: alice sees her own 7 remaining cards", len(_hand(va, "alice")) == 7)
check("imperfect info: opp sees alice's hand COUNT (7) but not identities",
      ("alice", 7) in vb.get("hand_count", set()) and not any(pp == "alice" for (pp, _c) in vb.get("in_hand", set())))
check("imperfect info: the discarded cards are public in the graveyard for both seats",
      len(va.get("graveyard", set())) == 3 and len(vb.get("graveyard", set())) == 3)

# ====================== #2 players/opponents_cant_gain_life -> life-gain prevention =================
def _life_state(statics=()):
    s = {"is_player": {("alice",), ("bob",)}, "life": {("alice", 20), ("bob", 20)},
         "on_battlefield": set(), "printed_control": set(), "instance_of": set(), "static_player": set()}
    for j, (ctrl, slug, param) in enumerate(statics):
        c = f"perm{j}"; s["on_battlefield"].add((c,)); s["printed_control"].add((ctrl, c))
        s["instance_of"].add((c, slug)); s["static_player"].add((slug, param))
    return s
def _life(s, p):
    return next(v for (q, v) in s["life"] if q == p)

# baseline: no static -> normal gain
s = _life_state(); driver._adjust_life(s, "alice", 5)
check("no static: gain 5 -> 25", _life(s, "alice") == 25)
# players_cant_gain_life (Witch Hunt) prevents EVERYONE'S gain
s = _life_state([("alice", "witch_hunt", "players_cant_gain_life")])
driver._adjust_life(s, "alice", 5); driver._adjust_life(s, "bob", 5)
check("players_cant_gain_life: alice's gain prevented (20)", _life(s, "alice") == 20)
check("players_cant_gain_life: bob's gain prevented too (20)", _life(s, "bob") == 20)
# but it does NOT prevent life LOSS
s = _life_state([("alice", "witch_hunt", "players_cant_gain_life")]); driver._adjust_life(s, "alice", -4)
check("players_cant_gain_life does NOT affect life loss (16)", _life(s, "alice") == 16)
# opponents_cant_gain_life (Tibalt under alice) -> opponents can't gain, controller still can
s = _life_state([("alice", "tibalt_rakish_instigator", "opponents_cant_gain_life")])
driver._adjust_life(s, "bob", 5); driver._adjust_life(s, "alice", 5)
check("opponents_cant_gain_life: opponent bob's gain prevented (20)", _life(s, "bob") == 20)
check("opponents_cant_gain_life: controller alice STILL gains (25)", _life(s, "alice") == 25)
# prevention takes precedence over a life-gain doubler the same player controls
s = _life_state([("alice", "witch_hunt", "players_cant_gain_life")])
s["life_repl"] = {("alhammarret_s_archive", "double")}
s["on_battlefield"].add(("arch",)); s["printed_control"].add(("alice", "arch"))
s["instance_of"].add(("arch", "alhammarret_s_archive"))
driver._adjust_life(s, "alice", 5)
check("prevention beats a doubler: gain still 0 (20)", _life(s, "alice") == 20)
# static_player not loaded -> no prevention
s = _life_state(); s.pop("static_player"); driver._adjust_life(s, "alice", 5)
check("static_player unloaded: normal gain (25)", _life(s, "alice") == 25)

# imperfect info: life totals are PUBLIC -> both seats see the prevented (unchanged) total
s = _life_state([("alice", "witch_hunt", "players_cant_gain_life")]); driver._adjust_life(s, "bob", 5)
va = observe.observe(s, "alice"); vb = observe.observe(s, "bob")
check("imperfect info: both seats see bob's life unchanged at 20",
      _life(va, "bob") == 20 and _life(vb, "bob") == 20)

print(f"\n{_ok[1]}/{_ok[0]} checks passed")
import sys; sys.exit(0 if _ok[1] == _ok[0] else 1)
