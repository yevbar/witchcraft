"""test_face_down.py — §708.2 face-down permanents are 2/2 colorless creatures with no name/types/abilities.
The engine suppresses the real card's characteristics at the copiable base when face_down(c) is set. Since
the internal `subtype`/`color` relations aren't surfaced as outputs, we prove their suppression through the
OBSERVABLE side effects: a subtype lord and a color anthem buff the face-up card but NOT the face-down one.
Run: python3 test_face_down.py"""

import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root on sys.path (test relocated into subfolder)
import driver

_ok = [0, 0]
def check(name, cond):
    _ok[0] += 1; _ok[1] += bool(cond)
    print(("  ok  " if cond else " FAIL"), name)

def _state(face_down: bool):
    # 'g' is really a 4/4 RED GOBLIN with flying. Two anthems test the two hidden characteristics:
    #   gl  = Goblin lord  ('other Goblins you control get +1/+1')  -> fires only if g has the goblin SUBTYPE
    #   rl  = red anthem   ('red creatures you control get +1/+1')  -> fires only if g has the red COLOR
    s = {
        "is_player": {("alice",)},
        "on_battlefield": {("g",), ("gl",), ("rl",)},
        "printed_type": {("g", "creature"), ("gl", "creature"), ("rl", "enchantment")},
        "printed_subtype": {("g", "goblin"), ("gl", "goblin")},
        "printed_color": {("g", "red"), ("gl", "red"), ("rl", "white")},
        "printed_power": {("g", 4), ("gl", 0)}, "printed_toughness": {("g", 4), ("gl", 0)},
        "printed_keyword": {("g", "flying")},
        "printed_control": {("alice", "g"), ("alice", "gl"), ("alice", "rl")},
        "instance_of": {("gl", "gls"), ("rl", "rls")},
        "card_ability": {("gls", "a0", "static"), ("rls", "a0", "static")},
        "card_effect": {("gls", "a0", 0, "modify_pt", "+1/+1", "other_goblins_you_control", "-", "-"),
                         ("rls", "a0", 0, "modify_pt", "+1/+1", "red_creatures_you_control", "-", "-")},
        "counter": set(), "tapped": set(),
    }
    if face_down:
        s["face_down"] = {("g",)}
    return s

def _read(s, c):
    o = driver.run(s, ["power", "eff_toughness", "has_keyword", "creature"])
    p = next((int(v) for (cc, v) in o["power"] if cc == c), None)
    t = next((int(v) for (cc, v) in o["eff_toughness"] if cc == c), None)
    return p, t, {v for (cc, v) in o["has_keyword"] if cc == c}, (c,) in o["creature"]

# face-UP: 4/4 + Goblin lord (+1/+1, has subtype) + red anthem (+1/+1, is red) = 6/6, flying, creature
p, t, kws, cre = _read(_state(False), "g")
check("face-up: 6/6 (4/4 + Goblin-subtype lord + red-color anthem)", (p, t) == (6, 6))
check("face-up: has flying", "flying" in kws)
check("face-up: is a creature", cre)

# face-DOWN: 2/2 (real P/T gone), NEITHER anthem fires (no subtype, no color), no flying, still a creature
p, t, kws, cre = _read(_state(True), "g")
check("face-down: 2/2 — real P/T suppressed", (p, t) == (2, 2))
check("face-down: Goblin lord did NOT fire -> no subtype", (p, t) == (2, 2))
check("face-down: red anthem did NOT fire -> colorless", (p, t) == (2, 2))
check("face-down: no flying — abilities suppressed", kws == set())
check("face-down: still a creature (2/2 body)", cre)

# === MANIFEST mechanic end to end: card_effect 'manifest' -> face-down 2/2 -> shim + observe ============
import effect_handlers, observe
effect_handlers.load()

# the interpreter -> mechanic link: the manifest effect ENCODES to the engine effect the driver applies
check("ENCODE manifest top_of_library -> ('manifest',1,'controller')",
      effect_handlers.ENCODE["manifest"]("manifest", "1", "top_of_library", "-") == ("manifest", 1, "controller"))

def _board():
    # alice has a real 5/5 red Dragon ('drg') on top of her library; bob is the opponent
    return {
        "is_player": {("alice",), ("bob",)},
        "on_battlefield": set(), "printed_control": set(),
        "in_library": {("alice", "drg")},
        "_lib_order": {"alice": ["drg"]},
        "instance_of": {("drg", "dragonslug")},
        "card_type": {("dragonslug", "creature")}, "card_subtype": {("dragonslug", "dragon")},
        "card_color": {("dragonslug", "red")}, "card_power": {("dragonslug", 5)}, "card_toughness": {("dragonslug", 5)},
        "counter": set(), "tapped": set(), "face_down": set(),
    }

s = _board()
effect_handlers.APPLY["manifest"](driver, s, "a0", 1, "controller", "src", "alice")
check("manifest moves the top card out of the library", ("alice", "drg") not in s["in_library"])
check("manifest puts it onto the battlefield", ("drg",) in s["on_battlefield"])
check("manifest marks it face_down", ("drg",) in s["face_down"])
check("manifest: the controller KNOWS what it manifested", ("alice", "drg") in s["known"])

# engine: the manifested Dragon is mechanically a 2/2 colorless creature (face-down body), NOT a 5/5
o = driver.run(s, ["power", "eff_toughness", "creature"])
pw = next((int(v) for (c, v) in o["power"] if c == "drg"), None)
tg = next((int(v) for (c, v) in o["eff_toughness"] if c == "drg"), None)
check("engine: manifested permanent is a 2/2 (real 5/5 suppressed)", (pw, tg) == (2, 2))
check("engine: manifested permanent is a creature", ("drg",) in o["creature"])

# observe: the OPPONENT sees a face-down permanent but NOT its identity; running the engine on bob's view
# still yields a 2/2 (face_down survives redaction). The CONTROLLER sees the real card.
vb = observe.observe(s, "bob")
check("observe(bob): sees the permanent exists", ("drg",) in vb.get("on_battlefield", set()))
check("observe(bob): identity hidden (no instance_of for the manifested card)",
      not any(c == "drg" for (c, _slug) in vb.get("instance_of", set())))
check("observe(bob): face_down survives -> engine on bob's view derives a 2/2",
      next((int(v) for (c, v) in driver.run(vb, ["power"])["power"] if c == "drg"), None) == 2)
va = observe.observe(s, "alice")
check("observe(alice): controller sees the real card identity (it's the Dragon)",
      ("drg", "dragonslug") in va.get("instance_of", set()))

# turn face up (the payoff): now a real 5/5 red Dragon, public to everyone
driver.turn_face_up(s, "drg")
o = driver.run(s, ["power"])
pw = next((int(v) for (c, v) in o["power"] if c == "drg"), None)
check("turn_face_up: now the real 5/5 Dragon", pw == 5)
check("turn_face_up: face_down cleared", ("drg",) not in s["face_down"])
check("turn_face_up: now public to the opponent too",
      ("drg", "dragonslug") in observe.observe(s, "bob").get("instance_of", set()))

# === MORPH / DISGUISE / FORETELL casting + turn-face-up in the ACTION SURFACE (env) =====================
import env

def _hand_state(kw, param):
    # alice holds 'mz', really a 4/4 green Beast with `kw` (morph/disguise/foretell) costing `param`
    return {
        "is_player": {("alice",), ("bob",)},
        "active_player": {("alice",)}, "current_step": {("precombat_main",)},
        "in_hand": {("alice", "mz")}, "in_library": set(),
        "instance_of": {("mz", "mzslug")}, "card_keyword": {("mzslug", kw)},
        "keyword_param": {("mzslug", kw, param)},
        "card_type": {("mzslug", "creature")}, "card_subtype": {("mzslug", "beast")},
        "card_color": {("mzslug", "green")}, "card_power": {("mzslug", 4)}, "card_toughness": {("mzslug", 4)},
        "mana_available": {("alice", 5)},
        "printed_control": set(), "on_battlefield": set(), "face_down": set(), "counter": set(), "tapped": set(),
    }

# MORPH: cast face down for {3} is an action in the surface; turn up for the morph cost {3}
s = _hand_state("morph", "3")
check("env action surface offers cast_face_down for a morph card with mana",
      ("cast_face_down", "alice", "mz") in env._face_down_actions(s, "alice"))
driver.cast_face_down(s, "mz", "alice")
check("cast_face_down: mz left hand", ("alice", "mz") not in s["in_hand"])
check("cast_face_down: mz on battlefield face down", ("mz",) in s["on_battlefield"] and ("mz",) in s["face_down"])
pw = next((int(v) for (c, v) in driver.run(s, ["power"])["power"] if c == "mz"), None)
check("morph face-down is a 2/2 in the engine", pw == 2)
check("observe(bob): morph identity hidden", not any(c == "mz" for (c, _x) in observe.observe(s, "bob").get("instance_of", set())))
check("observe(alice): caster knows the real card", ("mz", "mzslug") in observe.observe(s, "alice").get("instance_of", set()))
check("turn_up_cost(morph '3') == 3", driver.turn_up_cost(s, "mz") == 3)
check("gating: turn_face_up NOT offered while alice is short on mana (2 left after the {3} cast)",
      not any(a[0] == "turn_face_up" for a in env._face_down_actions(s, "alice")))
s["mana_available"] = {("alice", 5)}                          # a later turn, mana available again
check("env now offers turn_face_up for the face-down permanent (mana available)",
      ("turn_face_up", "alice", "mz") in env._face_down_actions(s, "alice"))
driver.turn_face_up(s, "mz")
pw = next((int(v) for (c, v) in driver.run(s, ["power"])["power"] if c == "mz"), None)
check("after turn_face_up: real 4/4 Beast", pw == 4)

# FORETELL: exile face down for {2} is an action; the card waits face down in exile
s = _hand_state("foretell", "4_r_r")
check("env action surface offers foretell for a foretell card with mana",
      ("foretell", "alice", "mz") in env._face_down_actions(s, "alice"))
driver.foretell(s, "mz", "alice")
check("foretell: mz left hand", ("alice", "mz") not in s["in_hand"])
check("foretell: mz exiled face down", ("mz",) in s.get("exile", set()) and ("mz",) in s["face_down"])
check("foretell: marked foretold (castable later)", ("mz",) in s.get("_foretold", set()))
check("observe(bob): foretold card identity hidden in exile",
      not any(c == "mz" for (c, _x) in observe.observe(s, "bob").get("instance_of", set())))
check("observe(alice): owner knows the foretold card", ("mz", "mzslug") in observe.observe(s, "alice").get("instance_of", set()))

# GATING: no mana -> no face-down actions offered
s = _hand_state("morph", "3"); s["mana_available"] = {("alice", 2)}
check("gating: <3 mana -> no cast_face_down offered", not any(a[0] == "cast_face_down" for a in env._face_down_actions(s, "alice")))

print(f"\n{_ok[1]}/{_ok[0]} checks passed")
import sys; sys.exit(0 if _ok[1] == _ok[0] else 1)
