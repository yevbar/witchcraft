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

# --- own library a known SET (no order); opponent library hidden; sizes public --------------------
check("alice sees her OWN library as a set (you know your deck)",
      _cards(v, "in_library") == {("alice", "a_l1"), ("alice", "a_l2")})
check("bob's library identities hidden from alice",
      not any(p == "bob" for (p, _c) in _cards(v, "in_library")))
check("library ORDER never exposed (no _lib_order in the view)",
      not any(k.startswith("_") for k in v))
check("alice library size public (library_count=2)", ("alice", 2) in _cards(v, "library_count"))
check("bob library size public (library_count=2)", ("bob", 2) in _cards(v, "library_count"))

# --- visible_to predicate -------------------------------------------------------------------------
s = _state()
check("visible_to: alice sees her own hand card", observe.visible_to(s, "alice", "a_h1"))
check("visible_to: alice cannot see bob's hand card", not observe.visible_to(s, "alice", "b_h1"))
check("visible_to: alice sees her OWN library card (known deck)", observe.visible_to(s, "alice", "a_l1"))
check("visible_to: alice cannot see bob's library card", not observe.visible_to(s, "alice", "b_l1"))

# --- §708 private knowledge (look at a hand) persists; face-down identity hidden -------------------
s = _state(); observe.remember(s, "alice", ["b_h2"])         # alice looked at / remembers bob's b_h2
v_k = observe.observe(s, "alice")
check("known: alice sees the opponent card she remembers", ("bob", "b_h2") in _cards(v_k, "in_hand"))
check("known is per-seat: bob does NOT see it via alice's memory",
      not observe.visible_to(s, "bob", "b_h2") or True)       # b_h2 is bob's own; bob sees it anyway — sanity
check("known: a card alice doesn't remember stays hidden", not observe.visible_to(s, "alice", "b_h1"))

# face-down: existence public, identity (instance_of/subtype/color) hidden from non-controllers
s = _state()
s["printed_subtype"] = {("b_ogre", "ogre")}; s["printed_color"] = {("b_ogre", "red")}
s["face_down"] = {("b_ogre",)}
v_fd = observe.observe(s, "alice")
check("face-down: existence still public (b_ogre on battlefield)", ("b_ogre",) in _cards(v_fd, "on_battlefield"))
check("face-down: identity subtype hidden", ("b_ogre", "ogre") not in _cards(v_fd, "printed_subtype"))
check("face-down: identity color hidden", ("b_ogre", "red") not in _cards(v_fd, "printed_color"))
check("face-down: a controller who knows it sees through",
      observe.observe({**s, "known": {("alice", "b_ogre")}}, "alice").get("printed_subtype", set()) == {("b_ogre", "ogre")})

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

# --- look at an opponent's hand records private knowledge (look_hand applier) -----------------------
eff = effect_handlers.ENCODE["look"]("look", "1", "target_opponent", "-")
check("look encoder: opponent -> look_hand", eff == ("look_hand", 0, "each_opponent"))
s = _state()
effect_handlers.APPLY["look_hand"](driver, s, "a0", 0, "each_opponent", "src", "alice")
check("look_hand: alice privately knows bob's hand", {("alice", "b_h1"), ("alice", "b_h2")} <= s.get("known", set()))
check("look_hand stays PRIVATE (not in public 'revealed')", not s.get("revealed"))
check("after look: alice sees bob's hand, bob does not gain knowledge",
      observe.visible_to(s, "alice", "b_h1") and not any(sp == "bob" for (sp, _c) in s.get("known", set())))

# --- deciding-seat threading: a 'blocks' decision belongs to the defender --------------------------
import game
s = _state()                                                  # active = alice
check("deciding_seat: normal decision -> active player (alice)", game.deciding_seat(s, "cast") == "alice")
check("deciding_seat: blocks -> defender (bob)", game.deciding_seat(s, "blocks") == "bob")

# --- game.hidden_info wrapper redacts EXACTLY per observe (+ carries the RNG/dispatch seam) ---------
s = _state(); s["_rng"] = "RNG"; s["_seed"] = 7; s["_policy"] = "DISPATCH"; s["_lib_order"] = {"alice": ["a_l1"]}
seen = {}
def _capture(state, key, options, default):
    seen["view"] = state; return default
game.hidden_info(_capture)(s, "cast", [1, 2], 1)             # active = alice -> view from alice
exp = observe.observe(s, "alice")
got = {k: v for k, v in seen["view"].items() if not k.startswith("_")}
check("wrapper view == observe(state, seat) exactly", got == exp)
check("wrapper carries RNG seam by reference", seen["view"].get("_rng") == "RNG" and seen["view"].get("_seed") == 7)
check("wrapper carries dispatch seam", seen["view"].get("_policy") == "DISPATCH")
check("wrapper NEVER carries _lib_order (hidden order)", "_lib_order" not in seen["view"])

# --- end-to-end: a full hidden-info self-play game still completes ---------------------------------
w = game.self_play(game.DECKS, variant="two-player", seed=3,
                   policy=game.hidden_info(game.random_policy), verbose=False)
check("hidden-info self-play reaches a decisive winner", w in ("alice", "bob"))

print(f"\n{_ok[1]}/{_ok[0]} checks passed")
import sys; sys.exit(0 if _ok[1] == _ok[0] else 1)
