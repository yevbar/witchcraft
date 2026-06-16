"""test_lands.py — §305.2 / §116.2a EXTRA land drops: the one-shot 'play an additional land this turn'
effect (effect_handlers/lands.py) and the continuous Exploration/Azusa permission (driver._static_extra_lands),
both feeding the driver's land-drop loop. Run: python3 test_lands.py"""
import driver, effect_handlers
effect_handlers.load()

_ok = [0, 0]
def check(name, cond):
    _ok[0] += 1; _ok[1] += bool(cond)
    print(("  ok  " if cond else " FAIL"), name)

def _st(nlands=3):
    return {
        "is_player": {("alice",)},
        "in_hand": {("alice", f"l{i}") for i in range(nlands)},
        "spell_type": {(f"l{i}", "land") for i in range(nlands)},
        "printed_type": {(f"l{i}", "land") for i in range(nlands)},
        "land_produces": {(f"l{i}", "green") for i in range(nlands)},
        "on_battlefield": set(), "printed_control": set(), "counter": set(), "tapped": set(),
    }

def _bf_lands(s):
    return sum(1 for (c,) in s.get("on_battlefield", set()) if (c, "land") in s.get("printed_type", set()))

# baseline: one land drop
s = _st()
driver._develop_mana(s, "alice")
check("baseline: exactly ONE land played", _bf_lands(s) == 1)

# the one-shot effect: ENCODE + APPLY grant an extra land, the loop plays a SECOND
check("ENCODE play additional_land -> ('extra_land',1,'controller')",
      effect_handlers.ENCODE["play"]("play", "-", "you", "additional_land_this_turn") == ("extra_land", 1, "controller"))
check("ENCODE play <other> abstains", effect_handlers.ENCODE["play"]("play", "-", "that_card", "-") is None)

s = _st()
effect_handlers.APPLY["extra_land"](driver, s, "a0", 1, "controller", "src", "alice")
check("APPLY extra_land bumps the grant", s.get("_extra_land_grants", {}).get("alice") == 1)
driver._develop_mana(s, "alice")
check("one-shot grant: TWO lands played (base + 1 extra)", _bf_lands(s) == 2)

# allowance is bounded by the grant: a single grant doesn't let a 3rd land in
s = _st(); driver._grant_extra_land(s, "alice", 1); driver._develop_mana(s, "alice")
check("grant of 1 -> at most 2 lands (not 3)", _bf_lands(s) == 2)

# continuous permission: Exploration (+1) / Azusa (+2) via static_player (when loaded)
s = _st(nlands=4)
s["on_battlefield"].add(("expl",)); s["printed_control"].add(("alice", "expl"))
s["instance_of"] = {("expl", "exploration")}
s["static_player"] = {("exploration", "extra_land_per_turn")}
check("_static_extra_lands reads Exploration as +1", driver._static_extra_lands(s, "alice") == 1)
driver._develop_mana(s, "alice")
check("Exploration: TWO lands played (base + static +1)", _bf_lands(s) == 2)

s = _st(nlands=4)
s["on_battlefield"].add(("az",)); s["printed_control"].add(("alice", "az"))
s["instance_of"] = {("az", "azusa")}; s["static_player"] = {("azusa", "extra_lands_per_turn_two")}
check("_static_extra_lands reads Azusa as +2", driver._static_extra_lands(s, "alice") == 2)
driver._develop_mana(s, "alice")
check("Azusa: THREE lands played (base + static +2)", _bf_lands(s) == 3)

# stacking: static +1 AND a one-shot grant +1 -> base + 2
s = _st(nlands=4)
s["on_battlefield"].add(("expl",)); s["printed_control"].add(("alice", "expl"))
s["instance_of"] = {("expl", "exploration")}; s["static_player"] = {("exploration", "extra_land_per_turn")}
driver._grant_extra_land(s, "alice", 1)
driver._develop_mana(s, "alice")
check("static +1 and one-shot +1 stack -> THREE lands", _bf_lands(s) == 3)

print(f"\n{_ok[1]}/{_ok[0]} checks passed")
import sys; sys.exit(0 if _ok[1] == _ok[0] else 1)
