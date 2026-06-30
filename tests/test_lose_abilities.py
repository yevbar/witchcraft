"""test_lose_abilities.py — §613 layer 6 "loses all abilities".

A creature that loses all abilities (loses_abilities(c), the shim-fed EDB) has NO keywords — neither its
printed keywords nor any GRANTED (anthem/lord) keyword — while its P/T, types and color are UNTOUCHED
(layer 6 removes only abilities). has_keyword is an .output relation, so we read it directly; P/T is read
via the power/eff_toughness outputs. The suppression is a PUBLIC board fact, so it holds identically on
the observed (imperfect-information) view of any seat. Plus the handler encode/apply path.
Run: python3 test_lose_abilities.py"""

import os, sys  # put repo root + packages/ on sys.path (file relocated; find root by the datalog/ marker)
_r = os.path.dirname(os.path.abspath(__file__))
while _r != os.path.dirname(_r) and not os.path.isdir(os.path.join(_r, "datalog")):
    _r = os.path.dirname(_r)
for _p in (_r, os.path.join(_r, "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import driver

_ok = [0, 0]
def check(name, cond):
    _ok[0] += 1; _ok[1] += bool(cond)
    print(("  ok  " if cond else " FAIL"), name)


def _state(lose: bool):
    # 'g' is a 4/4 RED GOBLIN with flying (printed). 'kw' is a static anthem that GRANTS trample to g
    #   ('creatures you control have trample') — tests that GRANTED keywords are suppressed too.
    s = {
        "is_player": {("alice",)},
        "on_battlefield": {("g",), ("kw",)},
        "printed_type": {("g", "creature"), ("kw", "enchantment")},
        "printed_subtype": {("g", "goblin")},
        "printed_color": {("g", "red"), ("kw", "white")},
        "printed_power": {("g", 4)}, "printed_toughness": {("g", 4)},
        "printed_keyword": {("g", "flying")},
        "printed_control": {("alice", "g"), ("alice", "kw")},
        # static anthem 'creatures you control have trample' -> static_grant_kw(kw, g, "trample")
        "instance_of": {("kw", "kws")},
        "card_ability": {("kws", "a0", "static")},
        "card_effect": {("kws", "a0", 0, "grant_keyword", "trample", "creatures_you_control", "-", "-")},
        "counter": set(), "tapped": set(),
    }
    if lose:
        s["loses_abilities"] = {("g",)}
    return s


def _read(s, c):
    o = driver.run(s, ["power", "eff_toughness", "has_keyword", "creature"])
    p = next((int(v) for (cc, v) in o["power"] if cc == c), None)
    t = next((int(v) for (cc, v) in o["eff_toughness"] if cc == c), None)
    return p, t, {v for (cc, v) in o["has_keyword"] if cc == c}, (c,) in o["creature"]


# CONTROL CASE (no lose_abilities): g has flying (printed) AND trample (granted by the anthem), stays 4/4.
p, t, kws, cre = _read(_state(False), "g")
check("control: 4/4 (printed P/T)", (p, t) == (4, 4))
check("control: has printed flying", "flying" in kws)
check("control: has GRANTED trample (anthem fired)", "trample" in kws)
check("control: is a creature", cre)

# LOSE-ALL CASE: g loses all abilities -> NO keywords at all; P/T, type, color untouched (still 4/4 creature).
p, t, kws, cre = _read(_state(True), "g")
check("loses-all: 4/4 — P/T UNCHANGED (layer 6 strips only abilities)", (p, t) == (4, 4))
check("loses-all: no flying (printed keyword suppressed)", "flying" not in kws)
check("loses-all: no trample (GRANTED anthem keyword suppressed)", "trample" not in kws)
check("loses-all: has ZERO keywords", kws == set())
check("loses-all: still a creature (type untouched)", cre)

# COLOR/TYPE untouched: a red-color anthem still buffs it (proves color survived layer 6, like face_down's
# anthem-differential). Add a red anthem and confirm the loses-abilities creature still gets +1/+1.
def _state_color(lose: bool):
    s = _state(lose)
    s["on_battlefield"].add(("rl",))
    s["printed_type"].add(("rl", "enchantment"))
    s["printed_color"].add(("rl", "white"))
    s["printed_control"].add(("alice", "rl"))
    s["instance_of"].add(("rl", "rls"))
    s["card_ability"].add(("rls", "a0", "static"))
    s["card_effect"].add(("rls", "a0", 0, "modify_pt", "+1/+1", "red_creatures_you_control", "-", "-"))
    return s

p, t, _kws, _cre = _read(_state_color(True), "g")
check("loses-all: red anthem STILL buffs it -> 5/5 (color survived layer 6)", (p, t) == (5, 5))


# === IMPERFECT-INFORMATION: the suppression holds on the observed view (public board state) ============
import observe
s_obs = _state(True)
s_obs["is_player"].add(("bob",))                          # bob is the opponent observing alice's board
view = observe.observe(s_obs, "bob")
check("imperfect: loses_abilities survives observe redaction (public board)",
      ("g",) in view.get("loses_abilities", set()))
p, t, kws, cre = _read(view, "g")
check("imperfect: opponent's view also sees g with no flying", "flying" not in kws)
check("imperfect: opponent's view also sees g with no keywords at all", kws == set())
check("imperfect: opponent's view still sees g as a 4/4 creature", (p, t) == (4, 4) and cre)


# === HANDLER encode/apply path ========================================================================
import effect_handlers
effect_handlers.load()

# extra == "-" (loses ALL): clean single-creature scopes ENCODE; board/multi/perpetual ABSTAIN.
check("ENCODE self -> ('lose_abilities',0,'self')",
      effect_handlers.ENCODE["lose_abilities"]("lose_abilities", "-", "self", "-") == ("lose_abilities", 0, "self"))
check("ENCODE it -> self scope",
      effect_handlers.ENCODE["lose_abilities"]("lose_abilities", "-", "it", "-") == ("lose_abilities", 0, "self"))
check("ENCODE enchanted_creature -> attached scope",
      effect_handlers.ENCODE["lose_abilities"]("lose_abilities", "-", "enchanted_creature", "-") == ("lose_abilities", 0, "attached"))
check("ENCODE target_creature -> target scope",
      effect_handlers.ENCODE["lose_abilities"]("lose_abilities", "-", "target_creature", "-") == ("lose_abilities", 0, "target"))
check("ENCODE board-scope all_creatures -> abstain (None)",
      effect_handlers.ENCODE["lose_abilities"]("lose_abilities", "-", "all_creatures", "-") is None)
check("ENCODE multi up_to_two_target_creatures_each -> abstain (None)",
      effect_handlers.ENCODE["lose_abilities"]("lose_abilities", "-", "up_to_two_target_creatures_each", "-") is None)
# extra NON-EMPTY ('loses flying') is eff_remove_keyword's job, not ours -> abstain.
check("ENCODE 'loses flying' (extra=flying) -> abstain (None — that's eff_remove_keyword)",
      effect_handlers.ENCODE["lose_abilities"]("lose_abilities", "-", "target_creature", "flying") is None)

# APPLY self: sets loses_abilities for the source.
s = _state(False)
effect_handlers.APPLY["lose_abilities"](driver, s, "a0", 0, "self", "g", "alice")
check("APPLY self: loses_abilities(g) set", ("g",) in s.get("loses_abilities", set()))
p, t, kws, cre = _read(s, "g")
check("APPLY self: g now has no flying", "flying" not in kws)
check("APPLY self: g P/T unchanged (4/4)", (p, t) == (4, 4))

# APPLY attached: an Aura 'au' enchanting 'g' strips g's abilities.
s = _state(False)
s["on_battlefield"].add(("au",))
s["attached_to"] = {("au", "g")}
effect_handlers.APPLY["lose_abilities"](driver, s, "a1b", 0, "attached", "au", "alice")
check("APPLY attached: loses_abilities(host g) set via attached_to", ("g",) in s.get("loses_abilities", set()))
_p, _t, kws, _cre = _read(s, "g")
check("APPLY attached: enchanted creature loses flying", "flying" not in kws)

# APPLY target: strips the strongest enemy creature.
s = _state(False)
s["is_player"].add(("bob",))
s["on_battlefield"].add(("e",))
s["printed_type"].add(("e", "creature"))
s["printed_power"].add(("e", 3)); s["printed_toughness"].add(("e", 3))
s["printed_keyword"].add(("e", "flying"))
s["printed_control"].add(("bob", "e"))
effect_handlers.APPLY["lose_abilities"](driver, s, "a0", 0, "target", "g", "alice")
check("APPLY target: an opponent's creature was stripped", ("e",) in s.get("loses_abilities", set()))
_p, _t, kws, _cre = _read(s, "e")
check("APPLY target: targeted enemy lost flying", "flying" not in kws)


print(f"\n{_ok[1]}/{_ok[0]} passed")
import sys
sys.exit(0 if _ok[1] == _ok[0] else 1)
