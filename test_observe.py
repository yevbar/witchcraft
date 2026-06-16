"""test_observe.py — imperfect-information projection (observe.py) + reveal visibility.
Run: python3 test_observe.py"""
import observe
import effect_handlers
effect_handlers.load()
import driver

_ok = [0, 0]
def check(name, cond):
    _ok[0] += 1; _ok[1] += bool(cond)
    print(("  ok  " if cond else " FAIL"), name)

def _state():
    # alice & bob each: 1 battlefield creature, 2 hand cards, 2 library cards. alice is the observer.
    return {
        "is_player": {("alice",), ("bob",)},
        "active_player": {("alice",)}, "current_step": {("precombat_main",)},
        "life": {("alice", 20), ("bob", 18)},
        "on_battlefield": {("a_bear",), ("b_ogre",)},
        "printed_type": {("a_bear", "creature"), ("b_ogre", "creature"),
                          ("a_h1", "creature"), ("a_h2", "instant"),
                          ("b_h1", "sorcery"), ("b_h2", "creature"),
                          ("a_l1", "land"), ("b_l1", "land")},
        "printed_control": {("alice", "a_bear"), ("bob", "b_ogre")},
        "in_hand": {("alice", "a_h1"), ("alice", "a_h2"), ("bob", "b_h1"), ("bob", "b_h2")},
        "in_library": {("alice", "a_l1"), ("alice", "a_l2"), ("bob", "b_l1"), ("bob", "b_l2")},
        "instance_of": {("a_h1", "grizzly_bears"), ("b_h1", "lightning_bolt"),
                         ("a_l1", "forest"), ("b_l1", "island")},
    }

def _cards(view, rel):
    return view.get(rel, set())

# --- public info is visible to the observer ---------------------------------------------------------
v = observe.observe(_state(), "alice")
check("public: both battlefield creatures visible", _cards(v, "on_battlefield") == {("a_bear",), ("b_ogre",)})
check("public: both players' life visible", _cards(v, "life") == {("alice", 20), ("bob", 18)})

# --- own hand visible, opponent hand hidden --------------------------------------------------------
check("own hand visible to alice", _cards(v, "in_hand") >= {("alice", "a_h1"), ("alice", "a_h2")})
check("bob's hand identities hidden from alice",
      not any(p == "bob" for (p, _c) in _cards(v, "in_hand")))
check("bob's hand size still public (hand_count=2)", ("bob", 2) in _cards(v, "hand_count"))
check("bob's hand card characteristics scrubbed (no b_h1 printed_type)",
      not any(c in ("b_h1", "b_h2") for (c, _t) in _cards(v, "printed_type")))
check("bob's hand instance_of scrubbed (lightning_bolt identity not leaked)",
      not any(c in ("b_h1", "b_h2") for (c, _s) in _cards(v, "instance_of")))

# --- libraries hidden for BOTH players, sizes public ----------------------------------------------
check("no library membership leaks (even alice's own)", _cards(v, "in_library") == set())
check("alice library size public (library_count=2)", ("alice", 2) in _cards(v, "library_count"))
check("bob library size public (library_count=2)", ("bob", 2) in _cards(v, "library_count"))

# --- visible_to predicate -------------------------------------------------------------------------
s = _state()
check("visible_to: alice sees her own hand card", observe.visible_to(s, "alice", "a_h1"))
check("visible_to: alice cannot see bob's hand card", not observe.visible_to(s, "alice", "b_h1"))
check("visible_to: nobody sees a library card", not observe.visible_to(s, "alice", "a_l1"))

# --- reveal makes a hidden card visible -----------------------------------------------------------
s = _state()
s["revealed"] = {("b_h1",)}
v2 = observe.observe(s, "alice")
check("revealed opponent card becomes visible in alice's view",
      ("bob", "b_h1") in _cards(v2, "in_hand"))
check("revealed card's characteristics return", ("b_h1", "sorcery") in _cards(v2, "printed_type"))
check("non-revealed opponent card still hidden", observe.visible_to(s, "alice", "b_h2") is False)

# --- reveal handler: 'reveal target opponent's hand' marks the whole hand revealed -----------------
eff = effect_handlers.ENCODE["reveal"]("reveal", "-", "target_opponent", "hand")
check("reveal encoder: opponent hand -> reveal_hand/each_opponent", eff == ("reveal_hand", 0, "each_opponent"))
s = _state()
effect_handlers.APPLY["reveal_hand"](driver, s, "a0", 0, "each_opponent", "src", "alice")
check("apply reveal_hand: bob's whole hand now in revealed", {("b_h1",), ("b_h2",)} <= s.get("revealed", set()))
v3 = observe.observe(s, "alice")
check("after reveal: alice sees bob's full hand", {("bob", "b_h1"), ("bob", "b_h2")} <= _cards(v3, "in_hand"))

# --- reveal top of library ------------------------------------------------------------------------
s = _state(); s["_lib_order"] = {"alice": ["a_l1", "a_l2"]}
effect_handlers.APPLY["reveal_top"](driver, s, "a0", 1, "controller", "src", "alice")
check("apply reveal_top(1): only the top library card revealed", s.get("revealed", set()) == {("a_l1",)})

# --- choose / pay are transparent no-ops ----------------------------------------------------------
check("choose encodes to noop", effect_handlers.ENCODE["choose"]("choose", "-", "-", "-") == ("noop", 0, "-"))
check("pay encodes to noop", effect_handlers.ENCODE["pay"]("pay", "2", "-", "-") == ("noop", 0, "-"))

# --- game.hidden_info wrapper: the inner policy only ever sees a redacted view ---------------------
import game
_saw_opp_hand = [False]
def _spy_policy(state, key, options, default):
    # the active seat must NOT see any opponent's hand identities, nor any library membership
    seat = next(iter(state.get("active_player", [("?",)])))[0]
    for (p, _c) in state.get("in_hand", set()):
        if p != seat:
            _saw_opp_hand[0] = True
    if state.get("in_library"):
        _saw_opp_hand[0] = True
    return game.random_policy(state, key, options, default)

w = game.self_play(game.DECKS, variant="two-player", seed=3,
                   policy=game.hidden_info(_spy_policy), verbose=False)
check("hidden-info self-play reaches a decisive winner", w in ("alice", "bob"))
check("wrapped policy NEVER saw an opponent's hand or any library", not _saw_opp_hand[0])

print(f"\n{_ok[1]}/{_ok[0]} checks passed")
import sys; sys.exit(0 if _ok[1] == _ok[0] else 1)
